"""
CS:GO Real-Time Signals via Redis Streams.

Provides signal publishing and consumption for CS:GO trading strategies.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import redis.asyncio as aioredis

from src.config.settings import settings

logger = logging.getLogger(__name__)

# Redis stream configuration
CSGO_SIGNALS_STREAM = "csgo:signals"
MAX_STREAM_LENGTH = 10000  # Keep last 10K signals


async def get_async_redis() -> aioredis.Redis:
    """Get an async Redis client."""
    return aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


async def publish_csgo_signal(signal: dict[str, Any]) -> str:
    """
    Publish a CS:GO signal to Redis stream.

    Args:
        signal: Signal data dict

    Returns:
        Stream message ID
    """
    redis = await get_async_redis()
    try:
        # Serialize complex types
        serialized = {}
        for k, v in signal.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                serialized[k] = json.dumps(v)
            else:
                serialized[k] = str(v)

        # Add to stream with auto-trim
        message_id = await redis.xadd(
            CSGO_SIGNALS_STREAM,
            serialized,
            maxlen=MAX_STREAM_LENGTH,
        )

        logger.debug(f"Published CS:GO signal: {message_id}")
        return message_id

    except Exception as e:
        logger.error(f"Failed to publish CS:GO signal: {e}")
        return ""
    finally:
        await redis.close()


async def consume_csgo_signals(
    consumer_group: str = "csgo-strategies",
    consumer_name: str = "strategy-1",
    block_ms: int = 1000,
    count: int = 10,
) -> AsyncIterator[dict[str, Any]]:
    """
    Consume CS:GO signals from Redis stream.

    Creates consumer group if it doesn't exist.
    Uses consumer groups for reliable delivery.

    Args:
        consumer_group: Consumer group name
        consumer_name: Consumer instance name
        block_ms: Block timeout in milliseconds
        count: Max messages to read at once

    Yields:
        Signal dicts with deserialized data
    """
    redis = await get_async_redis()
    try:
        # Create consumer group if not exists
        try:
            await redis.xgroup_create(
                CSGO_SIGNALS_STREAM,
                consumer_group,
                id="0",  # Start from beginning
                mkstream=True,
            )
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        while True:
            try:
                # Read new messages
                messages = await redis.xreadgroup(
                    consumer_group,
                    consumer_name,
                    {CSGO_SIGNALS_STREAM: ">"},
                    block=block_ms,
                    count=count,
                )

                for stream_name, stream_messages in messages:
                    for message_id, data in stream_messages:
                        # Deserialize
                        signal = {}
                        for k, v in data.items():
                            try:
                                # Try to parse as JSON
                                signal[k] = json.loads(v)
                            except (json.JSONDecodeError, TypeError):
                                signal[k] = v

                        signal["_message_id"] = message_id
                        yield signal

                        # Acknowledge
                        await redis.xack(CSGO_SIGNALS_STREAM, consumer_group, message_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error consuming CS:GO signals: {e}")
                await asyncio.sleep(1)

    finally:
        await redis.close()


async def get_recent_signals(
    count: int = 100,
    condition_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Get recent CS:GO signals from the stream.

    Args:
        count: Number of signals to retrieve
        condition_id: Optional filter by condition ID

    Returns:
        List of signal dicts, newest first
    """
    redis = await get_async_redis()
    try:
        # Read last N messages
        messages = await redis.xrevrange(
            CSGO_SIGNALS_STREAM,
            count=count * 2 if condition_id else count,
        )

        signals = []
        for message_id, data in messages:
            # Deserialize
            signal = {}
            for k, v in data.items():
                try:
                    signal[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    signal[k] = v

            signal["_message_id"] = message_id

            # Filter by condition_id if specified
            if condition_id and signal.get("condition_id") != condition_id:
                continue

            signals.append(signal)

            if len(signals) >= count:
                break

        return signals

    except Exception as e:
        logger.error(f"Failed to get recent signals: {e}")
        return []
    finally:
        await redis.close()


async def get_stream_stats() -> dict[str, Any]:
    """
    Get statistics about the CS:GO signals stream.

    Returns:
        Dict with stream length, consumer groups, etc.
    """
    redis = await get_async_redis()
    try:
        info = await redis.xinfo_stream(CSGO_SIGNALS_STREAM)
        return {
            "length": info.get("length", 0),
            "first_entry": info.get("first-entry"),
            "last_entry": info.get("last-entry"),
            "radix_tree_keys": info.get("radix-tree-keys", 0),
        }
    except aioredis.ResponseError:
        return {"length": 0, "error": "Stream does not exist"}
    except Exception as e:
        logger.error(f"Failed to get stream stats: {e}")
        return {"length": 0, "error": str(e)}
    finally:
        await redis.close()
