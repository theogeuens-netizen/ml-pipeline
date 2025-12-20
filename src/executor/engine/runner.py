"""
Executor Runner.

Main loop that orchestrates:
1. Market scanning
2. Strategy execution
3. Signal generation
4. Risk checking
5. Order execution
6. Position updates

Strategies are loaded from strategies.yaml (config-driven).
All decisions are logged to trade_decisions table for audit trail.
"""

import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy.orm import Session

from src.db.database import get_session
from src.executor.config import ExecutorConfig, TradingMode, get_config, reload_config, check_config_changed
from src.executor.execution.paper import PaperExecutor, OrderbookState
from src.executor.models import Signal as SignalModel, SignalStatus, TradeDecision
from src.executor.portfolio import PositionManager, RiskManager, PositionSizer
from src.executor.strategies import get_registry
from src.executor.strategies.base import MarketData, Signal
from src.alerts.telegram import alert_trade, alert_error
from strategies.loader import load_strategies
from strategies.base import Strategy as FileStrategy
from .scanner import MarketScanner

logger = logging.getLogger(__name__)

# Path to strategies config
STRATEGIES_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "strategies.yaml"


class ExecutorRunner:
    """
    Main executor loop.

    Orchestrates the entire trading pipeline:
    - Loads and reloads configuration
    - Scans markets for opportunities
    - Runs enabled strategies
    - Checks risk limits
    - Executes approved signals
    - Updates positions
    """

    def __init__(self, config: Optional[ExecutorConfig] = None):
        """
        Initialize the executor runner.

        Args:
            config: Executor configuration (loads from file if None)
        """
        self.config = config or get_config()
        self.scanner = MarketScanner(self.config)
        self.risk_manager = RiskManager(self.config, is_paper=True)
        self.position_sizer = PositionSizer(self.config)
        self.position_manager = PositionManager(is_paper=True)

        # Paper executor (live executor added later)
        self.paper_executor = PaperExecutor()

        # Config-driven strategies from strategies.yaml
        self.deployed_strategies: list[FileStrategy] = []
        self._load_deployed_strategies()

        # State
        self.running = False
        self.last_scan_at: Optional[datetime] = None
        self.signals_generated = 0
        self.signals_executed = 0
        self._last_strategies_mtime: Optional[float] = None

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _load_deployed_strategies(self):
        """Load strategies from strategies.yaml (config-driven)."""
        self.deployed_strategies = []

        if not STRATEGIES_CONFIG_PATH.exists():
            logger.info("No strategies.yaml found, using legacy config.yaml strategies")
            return

        try:
            self._last_strategies_mtime = STRATEGIES_CONFIG_PATH.stat().st_mtime

            # Use the config-driven loader
            self.deployed_strategies = load_strategies(
                config_path=STRATEGIES_CONFIG_PATH,
                enabled_only=True,
            )

            for strategy in self.deployed_strategies:
                logger.info(
                    f"Loaded strategy: {strategy.name} v{strategy.version} "
                    f"({type(strategy).__name__})"
                )

            logger.info(f"Loaded {len(self.deployed_strategies)} strategies from strategies.yaml")

        except Exception as e:
            logger.error(f"Error loading strategies: {e}", exc_info=True)

    def _check_strategies_changed(self) -> bool:
        """Check if strategies.yaml has been modified."""
        if not STRATEGIES_CONFIG_PATH.exists():
            return False

        try:
            current_mtime = STRATEGIES_CONFIG_PATH.stat().st_mtime
            if self._last_strategies_mtime is None:
                return False
            return current_mtime > self._last_strategies_mtime
        except Exception:
            return False

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info("Shutdown signal received, stopping...")
        self.running = False

    def run(self):
        """
        Run the main executor loop.

        Continuously:
        1. Checks for config changes
        2. Checks for strategy changes
        3. Scans markets
        4. Runs strategies
        5. Executes signals
        6. Sleeps until next scan
        """
        logger.info(f"Starting executor in {self.config.mode.value} mode")
        logger.info(f"Deployed strategies: {len(self.deployed_strategies)}")
        self.running = True

        while self.running:
            try:
                # Check for config changes
                if check_config_changed():
                    logger.info("Config file changed, reloading...")
                    self.config = reload_config()
                    self._update_components()

                # Check for strategy changes
                if self._check_strategies_changed():
                    logger.info("Deployed strategies changed, reloading...")
                    self._load_deployed_strategies()

                # Run one scan cycle
                self.run_once()

                # Sleep until next scan
                interval = self.config.settings.scan_interval_seconds
                logger.debug(f"Sleeping {interval}s until next scan")
                time.sleep(interval)

            except Exception as e:
                logger.error(f"Error in executor loop: {e}", exc_info=True)
                alert_error("executor", str(e), "Main loop error")
                time.sleep(10)  # Back off on error

        logger.info("Executor stopped")

    def run_once(self):
        """
        Run a single scan cycle.

        1. Get scannable markets
        2. Run enabled strategies
        3. Process generated signals
        """
        start_time = time.time()
        self.last_scan_at = datetime.now(timezone.utc)

        try:
            with get_session() as db:
                # Get markets
                markets = self.scanner.get_scannable_markets(db)
                logger.info(f"Scanning {len(markets)} markets")

                # Get balance
                balance = self.paper_executor.get_balance()

                # Run strategies and collect signals
                signals = self._run_strategies(markets, db)

                # Process signals through risk manager
                approved_signals = self._process_signals(signals, balance, db)

                # Execute approved signals
                self._execute_signals(approved_signals, db)

                # Update position prices
                self._update_positions(markets, db)

        except Exception as e:
            logger.error(f"Error in scan cycle: {e}", exc_info=True)

        elapsed = time.time() - start_time
        logger.info(
            f"Scan cycle complete in {elapsed:.2f}s - "
            f"generated={self.signals_generated}, executed={self.signals_executed}"
        )

    def _update_components(self):
        """Update components after config reload."""
        self.scanner = MarketScanner(self.config)
        self.risk_manager = RiskManager(self.config, is_paper=True)
        self.position_sizer = PositionSizer(self.config)

    def _run_strategies(
        self,
        markets: list[MarketData],
        db: Session,
    ) -> list[Signal]:
        """
        Run all enabled strategies on markets.

        Runs both:
        1. Config-driven strategies from strategies.yaml
        2. Legacy config-based strategies from config.yaml (if any)

        Args:
            markets: Markets to scan
            db: Database session

        Returns:
            List of generated signals
        """
        signals = []

        # Convert MarketData from executor format to strategies format
        # (They share the same structure now)
        from strategies.base import MarketData as StrategyMarketData
        strategy_markets = []
        for m in markets:
            sm = StrategyMarketData(
                id=m.id,
                condition_id=m.condition_id,
                question=m.question,
                yes_token_id=m.yes_token_id,
                no_token_id=m.no_token_id,
                price=m.price,
                best_bid=m.best_bid,
                best_ask=m.best_ask,
                spread=m.spread,
                hours_to_close=m.hours_to_close,
                end_date=m.end_date,
                volume_24h=m.volume_24h,
                liquidity=m.liquidity,
                category=m.category,
                event_id=m.event_id,
                price_history=m.price_history,
                snapshot=m.raw,  # Pass raw data as snapshot for audit
            )
            strategy_markets.append(sm)

        # Run config-driven strategies from strategies.yaml
        for strategy in self.deployed_strategies:
            try:
                # Pre-filter markets
                filtered_markets = [m for m in strategy_markets if strategy.filter(m)]
                logger.debug(
                    f"Strategy {strategy.name}: {len(filtered_markets)}/{len(strategy_markets)} markets after filter"
                )

                # Run strategy
                for sig in strategy.scan(filtered_markets):
                    # Ensure strategy name and SHA are set
                    sig.strategy_name = strategy.name
                    sig.strategy_sha = strategy.get_sha()
                    signals.append(sig)
                    self.signals_generated += 1

            except Exception as e:
                logger.error(f"Error running strategy {strategy.name}: {e}", exc_info=True)
                alert_error(f"strategy.{strategy.name}", str(e))

        # Fall back to legacy config-based strategies if no deployed strategies
        if not self.deployed_strategies:
            registry = get_registry()
            for strategy_name, strategy_config in self.config.strategies.items():
                if not strategy_config.enabled:
                    continue

                try:
                    # Get or create strategy instance
                    strategy = registry.get_or_create_strategy(
                        strategy_name,
                        strategy_config.params,
                    )

                    if strategy is None:
                        logger.warning(f"Strategy not found: {strategy_name}")
                        continue

                    # Pre-filter markets
                    filtered_markets = [m for m in markets if strategy.filter(m)]
                    logger.debug(
                        f"Strategy {strategy_name}: {len(filtered_markets)}/{len(markets)} markets after filter"
                    )

                    # Run strategy
                    for signal in strategy.scan(filtered_markets):
                        signal.strategy_name = strategy_name
                        signals.append(signal)
                        self.signals_generated += 1

                except Exception as e:
                    logger.error(f"Error running strategy {strategy_name}: {e}", exc_info=True)
                    alert_error(f"strategy.{strategy_name}", str(e))

        logger.info(f"Strategies generated {len(signals)} signals")
        return signals

    def _process_signals(
        self,
        signals: list[Signal],
        balance: float,
        db: Session,
    ) -> list[tuple[Signal, SignalModel, TradeDecision]]:
        """
        Process signals through risk manager.

        Creates TradeDecision audit records for every signal.

        Args:
            signals: Generated signals
            balance: Current balance
            db: Database session

        Returns:
            List of (Signal, SignalModel, TradeDecision) tuples for approved signals
        """
        approved = []

        for signal in signals:
            # Create Signal database record
            signal_model = SignalModel(
                strategy_name=signal.strategy_name,
                market_id=signal.market_id,
                token_id=signal.token_id,
                side=signal.side.value if hasattr(signal.side, 'value') else signal.side,
                reason=signal.reason,
                edge=signal.edge,
                confidence=signal.confidence,
                price_at_signal=signal.price_at_signal,
                best_bid=signal.best_bid,
                best_ask=signal.best_ask,
                status=SignalStatus.PENDING.value,
            )

            # Create TradeDecision audit record
            decision = TradeDecision(
                strategy_name=signal.strategy_name,
                strategy_sha=getattr(signal, 'strategy_sha', 'unknown'),
                market_id=signal.market_id,
                condition_id=getattr(signal, 'condition_id', ''),
                market_snapshot=getattr(signal, 'market_snapshot', {}),
                decision_inputs=getattr(signal, 'decision_inputs', {}),
                signal_side=signal.side.value if hasattr(signal.side, 'value') else signal.side,
                signal_reason=signal.reason,
                signal_edge=signal.edge,
                executed=False,
            )

            # Check risk limits
            check = self.risk_manager.check_signal(signal, balance, db)

            if check.approved:
                # Calculate size
                size = self.position_sizer.calculate_size(
                    signal,
                    check.available_capital,
                )
                signal_model.suggested_size_usd = size
                signal.size_usd = size
                decision.signal_size_usd = size
                signal_model.status = SignalStatus.APPROVED.value
                approved.append((signal, signal_model, decision))
                logger.info(
                    f"Signal approved: {signal.strategy_name} {signal.side.value if hasattr(signal.side, 'value') else signal.side} "
                    f"${size:.2f} - {signal.reason}"
                )
            else:
                signal_model.status = SignalStatus.REJECTED.value
                signal_model.status_reason = check.reason
                decision.rejected_reason = check.reason
                logger.debug(
                    f"Signal rejected: {signal.strategy_name} - {check.reason}"
                )

            db.add(signal_model)
            db.add(decision)

        db.flush()
        return approved

    def _execute_signals(
        self,
        approved_signals: list[tuple[Signal, SignalModel, TradeDecision]],
        db: Session,
    ):
        """
        Execute approved signals.

        Updates TradeDecision with execution outcome and sends Telegram alerts.

        Args:
            approved_signals: List of (Signal, SignalModel, TradeDecision) tuples
            db: Database session
        """
        for signal, signal_model, decision in approved_signals:
            try:
                # Build orderbook state (in production, would fetch real orderbook)
                orderbook = OrderbookState(
                    best_bid=signal.best_bid,
                    best_ask=signal.best_ask,
                    mid_price=(signal.best_bid + signal.best_ask) / 2 if signal.best_bid and signal.best_ask else signal.price_at_signal,
                    bid_depth_10=1000,  # Simulated
                    ask_depth_10=1000,
                    spread=signal.best_ask - signal.best_bid if signal.best_bid and signal.best_ask else 0.01,
                )

                # Get execution config
                execution_config = self.config.get_effective_execution(signal.strategy_name)

                # Execute via paper executor
                result = self.paper_executor.execute_signal(
                    signal_model,
                    orderbook,
                    order_type=execution_config.default_order_type,
                    limit_offset_bps=execution_config.limit_offset_bps,
                    db=db,
                )

                if result.success:
                    signal_model.status = SignalStatus.EXECUTED.value
                    signal_model.processed_at = datetime.now(timezone.utc)
                    self.signals_executed += 1

                    # Update TradeDecision with execution info
                    decision.executed = True
                    decision.execution_price = result.executed_price
                    decision.position_id = result.position_id if hasattr(result, 'position_id') else None

                    logger.info(
                        f"Signal executed: {result.executed_shares:.2f} shares @ ${result.executed_price:.4f}"
                    )

                    # Send Telegram alert
                    side_str = signal.side.value if hasattr(signal.side, 'value') else signal.side
                    alert_trade(
                        strategy=signal.strategy_name,
                        side=side_str,
                        market=signal.reason,
                        price=result.executed_price,
                        size=float(signal_model.suggested_size_usd or 0),
                        edge=signal.edge,
                    )
                else:
                    signal_model.status = SignalStatus.REJECTED.value
                    signal_model.status_reason = result.message
                    decision.rejected_reason = result.message
                    logger.warning(f"Execution failed: {result.message}")

            except Exception as e:
                logger.error(f"Error executing signal: {e}", exc_info=True)
                signal_model.status = SignalStatus.REJECTED.value
                signal_model.status_reason = str(e)
                decision.rejected_reason = str(e)
                alert_error("executor", str(e), f"Failed to execute {signal.strategy_name} signal")

    def _update_positions(
        self,
        markets: list[MarketData],
        db: Session,
    ):
        """
        Update current prices for open positions.

        Args:
            markets: Current market data
            db: Database session
        """
        price_updates = {m.id: m.price for m in markets}
        self.position_manager.update_prices(price_updates, db)

    def stop(self):
        """Stop the executor gracefully."""
        logger.info("Stopping executor...")
        self.running = False

    def get_status(self) -> dict:
        """
        Get current executor status.

        Returns:
            Dictionary with status information
        """
        # Get enabled strategies from both sources
        enabled_strategies = []

        # File-based strategies
        for strategy in self.deployed_strategies:
            enabled_strategies.append({
                "name": strategy.name,
                "version": strategy.version,
                "sha": strategy.get_sha(),
                "source": "deployed",
            })

        # Config-based strategies (legacy, only if no deployed)
        if not self.deployed_strategies:
            for name, cfg in self.config.strategies.items():
                if cfg.enabled:
                    enabled_strategies.append({
                        "name": name,
                        "source": "config",
                    })

        return {
            "mode": self.config.mode.value,
            "running": self.running,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "signals_generated": self.signals_generated,
            "signals_executed": self.signals_executed,
            "balance": self.paper_executor.get_balance(),
            "enabled_strategies": enabled_strategies,
            "risk_status": self.risk_manager.get_risk_status(
                self.paper_executor.get_balance()
            ),
        }


def main():
    """Entry point for running the executor."""
    import structlog
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    logger.info("Starting Polymarket Executor...")
    runner = ExecutorRunner()
    runner.run()


if __name__ == "__main__":
    main()
