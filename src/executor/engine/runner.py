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
from collections import defaultdict

import yaml
from sqlalchemy.orm import Session

from src.db.database import get_session
from src.executor.config import ExecutorConfig, TradingMode, get_config, reload_config, check_config_changed
from src.executor.execution.paper import PaperExecutor, OrderbookState
from src.executor.models import Signal as SignalModel, SignalStatus, TradeDecision
from src.executor.portfolio import PositionManager, RiskManager, PositionSizer
from src.alerts.telegram import alert_trade, alert_error
from strategies.loader import load_strategies
from strategies.base import Strategy, MarketData, Signal
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
        self.deployed_strategies: list[Strategy] = []
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

                # Build market depth map for execution (use real orderbook data)
                market_depth_map = {
                    m.id: (
                        m.bid_depth_10 if m.bid_depth_10 else 1000.0,
                        m.ask_depth_10 if m.ask_depth_10 else 1000.0
                    )
                    for m in markets
                }

                # Get balance
                balance = self.paper_executor.get_balance()

                # Run strategies and collect signals
                signals = self._run_strategies(markets, db)

                # Process signals through risk manager
                approved_signals = self._process_signals(signals, balance, db)

                # Execute approved signals with real market depth
                self._execute_signals(approved_signals, market_depth_map, db)

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

        # Run config-driven strategies from strategies.yaml
        for strategy in self.deployed_strategies:
            try:
                # Pre-filter markets
                filtered_markets = [m for m in markets if strategy.filter(m)]
                logger.debug(
                    f"Strategy {strategy.name}: {len(filtered_markets)}/{len(markets)} markets after filter"
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
        seen_pairs: set[tuple[str, int]] = set()
        pending_by_strategy: defaultdict[str, int] = defaultdict(int)

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

            # Prevent duplicate signals per strategy/market in the same batch
            key = (signal.strategy_name, signal.market_id)
            duplicate_in_batch = key in seen_pairs
            if not duplicate_in_batch:
                seen_pairs.add(key)

            # Check risk limits
            check = self.risk_manager.check_signal(
                signal,
                balance,
                db,
                pending_positions=pending_by_strategy[signal.strategy_name],
            )

            if duplicate_in_batch:
                signal_model.status = SignalStatus.REJECTED.value
                signal_model.status_reason = (
                    f"Duplicate signal for {signal.strategy_name} on market {signal.market_id}"
                )
                decision.rejected_reason = signal_model.status_reason
                logger.debug(signal_model.status_reason)
            elif check.approved:
                # Calculate size using strategy's AVAILABLE capital for Kelly
                # Use current_usd (not allocated_usd) so sizing reflects capital not tied up in positions
                from src.executor.models import StrategyBalance
                strategy_balance_record = db.query(StrategyBalance).filter(
                    StrategyBalance.strategy_name == signal.strategy_name
                ).first()
                strategy_capital = float(strategy_balance_record.current_usd) if strategy_balance_record else 400.0

                size = self.position_sizer.calculate_size(
                    signal,
                    check.available_capital,
                    strategy_capital=strategy_capital,
                )
                signal_model.suggested_size_usd = size
                signal.size_usd = size
                decision.signal_size_usd = size
                signal_model.status = SignalStatus.APPROVED.value
                approved.append((signal, signal_model, decision))
                pending_by_strategy[signal.strategy_name] += 1
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
        market_depth_map: dict[int, tuple[float, float]],
        db: Session,
    ):
        """
        Execute approved signals.

        Updates TradeDecision with execution outcome and sends Telegram alerts.

        Args:
            approved_signals: List of (Signal, SignalModel, TradeDecision) tuples
            market_depth_map: Dict of market_id -> (bid_depth_10, ask_depth_10)
            db: Database session
        """
        for signal, signal_model, decision in approved_signals:
            try:
                # Final duplicate guard: skip if position already exists for this strategy/market
                existing = self.position_manager.get_position_by_market(
                    signal.market_id, db, strategy_name=signal.strategy_name
                )
                if existing is not None:
                    msg = (
                        f"Position already open for {signal.strategy_name} on market {signal.market_id}"
                    )
                    signal_model.status = SignalStatus.REJECTED.value
                    signal_model.status_reason = msg
                    decision.rejected_reason = msg
                    logger.info(msg)
                    continue

                # Get real orderbook depth from market data
                bid_depth, ask_depth = market_depth_map.get(
                    signal.market_id,
                    (1000.0, 1000.0)  # Fallback if not available
                )

                # Build orderbook state with real depth data
                orderbook = OrderbookState(
                    best_bid=signal.best_bid,
                    best_ask=signal.best_ask,
                    mid_price=(signal.best_bid + signal.best_ask) / 2 if signal.best_bid and signal.best_ask else signal.price_at_signal,
                    bid_depth_10=bid_depth,
                    ask_depth_10=ask_depth,
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

                    # Send Telegram alert with full market details
                    side_str = signal.side.value if hasattr(signal.side, 'value') else signal.side

                    # Get market title from database
                    from src.db.models import Market
                    market = db.query(Market).filter(Market.id == signal.market_id).first()
                    market_title = market.question if market else f"Market {signal.market_id}"

                    # Determine token side (YES or NO) from token_id
                    if market:
                        token_side = "YES" if signal.token_id == market.yes_token_id else "NO"
                    else:
                        token_side = "YES"  # Default

                    # Get expected win rate from strategy if available
                    expected_win_rate = None
                    for s in self.deployed_strategies:
                        if s.name == signal.strategy_name:
                            expected_win_rate = getattr(s, 'expected_no_rate', None)
                            break

                    # Get hours_to_close from decision_inputs
                    hours_to_close = None
                    if hasattr(signal, 'decision_inputs') and signal.decision_inputs:
                        hours_to_close = signal.decision_inputs.get('hours_to_close')

                    alert_trade(
                        strategy=signal.strategy_name,
                        side=side_str,
                        market_title=market_title,
                        market_id=signal.market_id,
                        token_side=token_side,
                        price=result.executed_price,
                        size=float(signal_model.suggested_size_usd or 0),
                        edge=signal.edge,
                        expected_win_rate=expected_win_rate,
                        order_type=execution_config.default_order_type.value,
                        best_bid=signal.best_bid,
                        best_ask=signal.best_ask,
                        hours_to_close=hours_to_close,
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

        Correctly handles YES vs NO tokens:
        - If position holds YES token, use YES price
        - If position holds NO token, use 1 - YES price

        Also fetches prices for positions in closed (but not resolved) markets,
        which wouldn't be in the scanner results but still need price tracking.

        Args:
            markets: Current market data from scanner
            db: Database session
        """
        from src.db.models import Market, Snapshot
        from src.executor.models import Position, PositionStatus
        from sqlalchemy import desc

        # Build market info map: market_id -> (yes_price, yes_token_id, no_token_id)
        market_info = {}
        for m in markets:
            market_info[m.id] = {
                'yes_price': m.price,
                'yes_token_id': m.yes_token_id,
                'no_token_id': m.no_token_id,
            }

        # Find open positions
        open_positions = db.query(Position).filter(
            Position.is_paper == True,
            Position.status == PositionStatus.OPEN.value,
        ).all()

        # Collect missing market IDs and fetch their info
        missing_market_ids = set()
        for pos in open_positions:
            if pos.market_id not in market_info:
                missing_market_ids.add(pos.market_id)

        # Fetch missing markets from database
        if missing_market_ids:
            missing_markets = db.query(Market).filter(
                Market.id.in_(missing_market_ids)
            ).all()

            for market in missing_markets:
                # Get latest price from snapshot
                latest_snapshot = db.query(Snapshot).filter(
                    Snapshot.market_id == market.id,
                ).order_by(desc(Snapshot.timestamp)).first()

                if latest_snapshot and latest_snapshot.price:
                    market_info[market.id] = {
                        'yes_price': float(latest_snapshot.price),
                        'yes_token_id': market.yes_token_id,
                        'no_token_id': market.no_token_id,
                    }
                    logger.debug(
                        f"Updated price for closed market {market.id} from snapshot: "
                        f"${latest_snapshot.price:.4f}"
                    )

        # Update each position with the correct token price
        for position in open_positions:
            if position.market_id not in market_info:
                continue

            info = market_info[position.market_id]
            yes_price = info['yes_price']

            # Determine if this position holds YES or NO tokens
            if position.token_id == info['yes_token_id']:
                current_price = yes_price
            elif position.token_id == info['no_token_id']:
                current_price = 1.0 - yes_price  # NO price = 1 - YES price
            else:
                # Token ID doesn't match either - shouldn't happen
                logger.warning(
                    f"Position {position.id} token_id doesn't match market tokens. "
                    f"Using YES price as fallback."
                )
                current_price = yes_price

            # Update position price and P&L
            position.current_price = current_price
            position.current_value = float(position.size_shares) * current_price
            position.unrealized_pnl = position.current_value - float(position.cost_basis)
            position.unrealized_pnl_pct = (
                position.unrealized_pnl / float(position.cost_basis)
                if position.cost_basis else 0
            )

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
    import sys
    import structlog

    # Configure basic logging to stdout
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        stream=sys.stdout,
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logger.info("Starting Polymarket Executor...")
    runner = ExecutorRunner()
    runner.run()


if __name__ == "__main__":
    main()
