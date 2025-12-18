"""
Executor WebSocket endpoint for real-time updates.

Provides real-time streaming of:
- New signals
- Trade executions
- Position updates
- Balance changes
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from src.db.database import get_db, get_session
from src.executor.models import Signal, ExecutorTrade, Position, PaperBalance
from src.executor.config import get_config

router = APIRouter()
logger = logging.getLogger(__name__)

# Track connected clients
connected_clients: list[WebSocket] = []

# Last seen IDs for polling
last_signal_id = 0
last_trade_id = 0


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        if not self.active_connections:
            return

        message_json = json.dumps(message, default=str)
        disconnected = []

        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


@router.websocket("/executor/ws")
async def executor_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time executor updates.

    Clients receive JSON messages with the following types:
    - signal: New signal generated
    - trade: Trade executed
    - position_update: Position updated
    - balance_update: Balance changed
    - status: Executor status update
    """
    await manager.connect(websocket)

    # Send initial status
    await send_status_update(websocket)

    try:
        # Start background polling task
        poll_task = asyncio.create_task(poll_updates(websocket))

        while True:
            try:
                # Wait for client messages (heartbeat, commands)
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0
                )

                try:
                    message = json.loads(data)
                    await handle_client_message(websocket, message)
                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Invalid JSON"
                    })

            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        poll_task.cancel()
        manager.disconnect(websocket)


async def handle_client_message(websocket: WebSocket, message: dict):
    """Handle incoming client messages."""
    msg_type = message.get("type")

    if msg_type == "ping":
        await websocket.send_json({
            "type": "pong",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    elif msg_type == "subscribe":
        # Future: selective subscriptions
        await websocket.send_json({
            "type": "subscribed",
            "channels": ["signals", "trades", "positions", "balance"]
        })

    elif msg_type == "get_status":
        await send_status_update(websocket)

    elif msg_type == "get_positions":
        await send_positions_update(websocket)


async def send_status_update(websocket: WebSocket):
    """Send current executor status."""
    config = get_config()

    with get_session() as db:
        # Get balance
        balance = db.query(PaperBalance).first()
        balance_usd = float(balance.balance_usd) if balance else 10000.0

        # Get position count
        from src.executor.models import PositionStatus
        open_positions = db.query(Position).filter(
            Position.status == PositionStatus.OPEN.value
        ).count()

    await websocket.send_json({
        "type": "status",
        "data": {
            "mode": config.mode.value,
            "balance": balance_usd,
            "open_positions": open_positions,
            "enabled_strategies": [
                name for name, cfg in config.strategies.items()
                if cfg.enabled
            ],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    })


async def send_positions_update(websocket: WebSocket):
    """Send current positions."""
    with get_session() as db:
        from src.executor.models import PositionStatus
        positions = db.query(Position).filter(
            Position.status == PositionStatus.OPEN.value
        ).all()

        await websocket.send_json({
            "type": "positions",
            "data": [
                {
                    "id": p.id,
                    "strategy_name": p.strategy_name,
                    "market_id": p.market_id,
                    "side": p.side,
                    "entry_price": float(p.entry_price) if p.entry_price else None,
                    "current_price": float(p.current_price) if p.current_price else None,
                    "size_shares": float(p.size_shares) if p.size_shares else None,
                    "cost_basis": float(p.cost_basis) if p.cost_basis else None,
                    "unrealized_pnl": float(p.unrealized_pnl) if p.unrealized_pnl else None,
                }
                for p in positions
            ]
        })


async def poll_updates(websocket: WebSocket):
    """Poll for new signals and trades."""
    global last_signal_id, last_trade_id

    # Initialize from DB
    with get_session() as db:
        latest_signal = db.query(Signal).order_by(desc(Signal.id)).first()
        latest_trade = db.query(ExecutorTrade).order_by(desc(ExecutorTrade.id)).first()

        last_signal_id = latest_signal.id if latest_signal else 0
        last_trade_id = latest_trade.id if latest_trade else 0

    while True:
        try:
            await asyncio.sleep(1.0)  # Poll every second

            with get_session() as db:
                # Check for new signals
                new_signals = db.query(Signal).filter(
                    Signal.id > last_signal_id
                ).order_by(Signal.id).all()

                for signal in new_signals:
                    await websocket.send_json({
                        "type": "signal",
                        "data": {
                            "id": signal.id,
                            "strategy_name": signal.strategy_name,
                            "market_id": signal.market_id,
                            "side": signal.side,
                            "status": signal.status,
                            "reason": signal.reason,
                            "edge": float(signal.edge) if signal.edge else None,
                            "confidence": float(signal.confidence) if signal.confidence else None,
                            "price_at_signal": float(signal.price_at_signal) if signal.price_at_signal else None,
                            "created_at": signal.created_at.isoformat() if signal.created_at else None,
                        }
                    })
                    last_signal_id = signal.id

                # Check for new trades
                new_trades = db.query(ExecutorTrade).filter(
                    ExecutorTrade.id > last_trade_id
                ).order_by(ExecutorTrade.id).all()

                for trade in new_trades:
                    await websocket.send_json({
                        "type": "trade",
                        "data": {
                            "id": trade.id,
                            "order_id": trade.order_id,
                            "side": trade.side,
                            "price": float(trade.price) if trade.price else None,
                            "size_shares": float(trade.size_shares) if trade.size_shares else None,
                            "size_usd": float(trade.size_usd) if trade.size_usd else None,
                            "is_paper": trade.is_paper,
                            "executed_at": trade.executed_at.isoformat() if trade.executed_at else None,
                        }
                    })
                    last_trade_id = trade.id

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(5.0)  # Back off on error


# Broadcast functions for external use

async def broadcast_signal(signal_data: dict):
    """Broadcast a new signal to all connected clients."""
    await manager.broadcast({
        "type": "signal",
        "data": signal_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


async def broadcast_trade(trade_data: dict):
    """Broadcast a new trade to all connected clients."""
    await manager.broadcast({
        "type": "trade",
        "data": trade_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


async def broadcast_position_update(position_data: dict):
    """Broadcast a position update to all connected clients."""
    await manager.broadcast({
        "type": "position_update",
        "data": position_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


async def broadcast_balance_update(balance: float, pnl: float):
    """Broadcast a balance update to all connected clients."""
    await manager.broadcast({
        "type": "balance_update",
        "data": {
            "balance": balance,
            "pnl": pnl,
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
