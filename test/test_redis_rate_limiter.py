import unittest
from unittest.mock import Mock

from redis.exceptions import ConnectionError as RedisConnectionError

from rate_limiter import RateLimitDecision
from redis_rate_limiter import RATE_LIMIT_SCRIPT, RedisRateLimiter


class RedisRateLimiterTests(unittest.TestCase):
    def test_converts_script_results_to_decisions(self) -> None:
        client = Mock()
        script = Mock(side_effect=[[1, 2, 0], [0, 0, 1501]])
        client.register_script.return_value = script
        limiter = RedisRateLimiter(
            client,
            max_requests=3,
            window_seconds=60,
            clock=lambda: 1000,
        )

        allowed = limiter.check("203.0.113.10")
        denied = limiter.check("203.0.113.10")

        self.assertEqual(allowed, RateLimitDecision(True, 2, 0))
        self.assertEqual(denied, RateLimitDecision(False, 0, 2))
        client.register_script.assert_called_once_with(RATE_LIMIT_SCRIPT)

        first_call = script.call_args_list[0]
        self.assertEqual(
            first_call.kwargs["keys"],
            ["weather_agent:rate_limit:v1:203.0.113.10"],
        )
        arguments = first_call.kwargs["args"]
        self.assertEqual(arguments[:3], [940_000, 3, 1_000_000])
        self.assertEqual(arguments[4], 60_000)
        self.assertIsInstance(arguments[3], str)

    def test_falls_back_when_redis_is_unavailable(self) -> None:
        client = Mock()
        script = Mock(
            side_effect=RedisConnectionError("Redis unavailable")
        )
        client.register_script.return_value = script
        fallback = Mock()
        fallback_decision = RateLimitDecision(True, 0)
        fallback.check.return_value = fallback_decision
        limiter = RedisRateLimiter(
            client,
            max_requests=1,
            window_seconds=60,
            fallback=fallback,
        )

        with self.assertLogs("redis_rate_limiter", level="WARNING"):
            decision = limiter.check("203.0.113.10")

        self.assertEqual(decision, fallback_decision)
        fallback.check.assert_called_once_with("203.0.113.10")


if __name__ == "__main__":
    unittest.main()
