import logging

from collections.abc import Callable
from math import ceil
from time import time
from uuid import uuid4

from redis import Redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
)

from rate_limiter import RateLimiter, RateLimitDecision

logger = logging.getLogger(__name__)

RATE_LIMIT_SCRIPT = """
local key = KEYS[1]

local cutoff_ms = tonumber(ARGV[1])
local max_requests = tonumber(ARGV[2])
local current_time_ms = tonumber(ARGV[3])
local request_id = ARGV[4]
local window_ms = tonumber(ARGV[5])

redis.call("ZREMRANGEBYSCORE", key, "-inf", cutoff_ms)

local request_count = redis.call("ZCARD", key)

if request_count >= max_requests then
    local oldest_request = redis.call(
        "ZRANGE",
        key,
        0,
        0,
        "WITHSCORES"
    )
    local oldest_time_ms = tonumber(oldest_request[2])
    local retry_after_ms = math.max(
        1,
        math.ceil(window_ms - (current_time_ms - oldest_time_ms))
    )

    return {0, 0, retry_after_ms}
end

redis.call("ZADD", key, current_time_ms, request_id)
redis.call("PEXPIRE", key, window_ms)

local remaining = max_requests - request_count - 1
return {1, remaining, 0}
"""


class RedisRateLimiter:
    """使用 Redis 有序集合实现跨进程共享的滑动窗口限流。"""

    def __init__(
        self,
        client: Redis,
        *,
        max_requests: int,
        window_seconds: float,
        fallback: RateLimiter | None = None,
        clock: Callable[[], float] = time,
    ) -> None:
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("请求数和时间窗口必须大于 0")

        self._max_requests = max_requests
        self._window_ms = ceil(window_seconds * 1000)
        self._fallback = fallback
        self._clock = clock
        self._script = client.register_script(RATE_LIMIT_SCRIPT)

    def _check_redis(self, client_id: str) -> RateLimitDecision:
        current_time_ms = int(self._clock() * 1000)
        key = f"weather_agent:rate_limit:v1:{client_id}"

        result = self._script(
            keys=[key],
            args=[
                current_time_ms - self._window_ms,
                self._max_requests,
                current_time_ms,
                uuid4().hex,
                self._window_ms,
            ],
        )

        allowed, remaining, retry_after_ms = (
            int(value) for value in result
        )

        return RateLimitDecision(
            allowed=bool(allowed),
            remaining=remaining,
            retry_after_seconds=ceil(retry_after_ms / 1000),
        )

    def check(self, client_id: str) -> RateLimitDecision:
        try:
            return self._check_redis(client_id)
        except (RedisConnectionError, RedisTimeoutError):
            if self._fallback is None:
                raise

            logger.warning(
                "Redis rate limiter unavailable; using in-memory fallback",
                exc_info=True,
            )
            return self._fallback.check(client_id)
