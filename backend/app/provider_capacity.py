"""One image admission budget shared by API processes and every worker replica."""

import asyncio
import logging
import random
from collections.abc import Awaitable
from functools import wraps
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import BlockingConnectionPool, Redis

logger = logging.getLogger(__name__)


def image_capacity(operation: Any) -> Any:
    @wraps(operation)
    async def guarded(gateway: Any, *args: Any, **kwargs: Any) -> Any:
        limiter = gateway._image_capacity
        if limiter is None:
            return await operation(gateway, *args, **kwargs)
        token = await limiter.acquire()
        # Unknown/cancelled calls retain their conservative two-hour lease.
        # Returning a slot is never grounds for replaying a successful POST.
        result = await operation(gateway, *args, **kwargs)
        if getattr(result.outcome, "value", result.outcome) != "outcome_unknown":
            try:
                await limiter.release(token)
            except Exception:
                logger.exception("Image capacity release deferred to lease expiry")
        return result

    return guarded


# Redis time avoids host clock skew; a single script makes admission atomic.
ACQUIRE = """
local now = redis.call('TIME')
local seconds = tonumber(now[1]) + tonumber(now[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', seconds)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('ZADD', KEYS[1], seconds + tonumber(ARGV[2]), ARGV[3])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) + 60, 'NX')
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) + 60, 'GT')
return 1
"""


class ImageCapacity:
    def __init__(self, url: str, key: str, limit: int, lease_seconds: int) -> None:
        self.client = Redis(
            connection_pool=BlockingConnectionPool.from_url(
                url,
                max_connections=32,
                timeout=10,
                socket_timeout=10,
                socket_connect_timeout=5,
            )
        )
        self.key = key
        self.limit = limit
        self.lease_seconds = lease_seconds

    async def acquire(self) -> str:
        token = uuid4().hex
        # Redis failure aborts admission before a paid request, never bypasses it.
        while not await cast(
            Awaitable[Any],
            self.client.eval(
                ACQUIRE,
                1,
                self.key,
                str(self.limit),
                str(self.lease_seconds),
                token,
            ),
        ):
            await asyncio.sleep(random.uniform(0.2, 0.5))
        return token

    async def release(self, token: str) -> None:
        await self.client.zrem(self.key, token)
