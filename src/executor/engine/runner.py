"""
Executor Runner.

Main loop that orchestrates:
1. Market scanning
2. Strategy execution
3. Signal generation
4. Risk checking
5. Order execution
6. Position updates
"""

import logging
import signal
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.executor.config import ExecutorConfig, TradingMode, get_config, reload_config, check_config_changed
from src.executor.execution.paper import PaperExecutor, OrderbookState
from src.executor.models import Signal as SignalModel, SignalStatus
from src.executor.portfolio import PositionManager, RiskManager, PositionSizer
from src.executor.strategies import get_registry, Signal
from src.executor.strategies.base import MarketData
from .scanner import MarketScanner

logger = logging.getLogger(__name__)


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

        # State
        self.running = False
        self.last_scan_at: Optional[datetime] = None
        self.signals_generated = 0
        self.signals_executed = 0

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info("Shutdown signal received, stopping...")
        self.running = False

    def run(self):
        """
        Run the main executor loop.

        Continuously:
        1. Checks for config changes
        2. Scans markets
        3. Runs strategies
        4. Executes signals
        5. Sleeps until next scan
        """
        logger.info(f"Starting executor in {self.config.mode.value} mode")
        self.running = True

        while self.running:
            try:
                # Check for config changes
                if check_config_changed():
                    logger.info("Config file changed, reloading...")
                    self.config = reload_config()
                    self._update_components()

                # Run one scan cycle
                self.run_once()

                # Sleep until next scan
                interval = self.config.settings.scan_interval_seconds
                logger.debug(f"Sleeping {interval}s until next scan")
                time.sleep(interval)

            except Exception as e:
                logger.error(f"Error in executor loop: {e}", exc_info=True)
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

        Args:
            markets: Markets to scan
            db: Database session

        Returns:
            List of generated signals
        """
        signals = []
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

        logger.info(f"Strategies generated {len(signals)} signals")
        return signals

    def _process_signals(
        self,
        signals: list[Signal],
        balance: float,
        db: Session,
    ) -> list[tuple[Signal, SignalModel]]:
        """
        Process signals through risk manager.

        Args:
            signals: Generated signals
            balance: Current balance
            db: Database session

        Returns:
            List of (Signal, SignalModel) tuples for approved signals
        """
        approved = []

        for signal in signals:
            # Create database record
            signal_model = SignalModel(
                strategy_name=signal.strategy_name,
                market_id=signal.market_id,
                token_id=signal.token_id,
                side=signal.side.value,
                reason=signal.reason,
                edge=signal.edge,
                confidence=signal.confidence,
                price_at_signal=signal.price_at_signal,
                best_bid=signal.best_bid,
                best_ask=signal.best_ask,
                status=SignalStatus.PENDING.value,
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
                signal.suggested_size_usd = size
                signal_model.status = SignalStatus.APPROVED.value
                approved.append((signal, signal_model))
                logger.info(
                    f"Signal approved: {signal.strategy_name} {signal.side.value} "
                    f"${size:.2f} - {signal.reason}"
                )
            else:
                signal_model.status = SignalStatus.REJECTED.value
                signal_model.status_reason = check.reason
                logger.debug(
                    f"Signal rejected: {signal.strategy_name} - {check.reason}"
                )

            db.add(signal_model)

        db.flush()
        return approved

    def _execute_signals(
        self,
        approved_signals: list[tuple[Signal, SignalModel]],
        db: Session,
    ):
        """
        Execute approved signals.

        Args:
            approved_signals: List of (Signal, SignalModel) tuples
            db: Database session
        """
        for signal, signal_model in approved_signals:
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
                    logger.info(
                        f"Signal executed: {result.executed_shares:.2f} shares @ ${result.executed_price:.4f}"
                    )
                else:
                    signal_model.status = SignalStatus.REJECTED.value
                    signal_model.status_reason = result.message
                    logger.warning(f"Execution failed: {result.message}")

            except Exception as e:
                logger.error(f"Error executing signal: {e}", exc_info=True)
                signal_model.status = SignalStatus.REJECTED.value
                signal_model.status_reason = str(e)

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
        return {
            "mode": self.config.mode.value,
            "running": self.running,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "signals_generated": self.signals_generated,
            "signals_executed": self.signals_executed,
            "balance": self.paper_executor.get_balance(),
            "enabled_strategies": [
                name for name, cfg in self.config.strategies.items()
                if cfg.enabled
            ],
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
