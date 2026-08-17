import os
import unittest
from unittest.mock import Mock, patch

from redis_client import (
    REDIS_SOCKET_TIMEOUT_SECONDS,
    check_redis_connection,
    create_redis_client,
)


class RedisClientTests(unittest.TestCase):
    @patch("redis_client.Redis.from_url")
    def test_creates_client_from_environment(self, from_url: Mock) -> None:
        with patch.dict(
            os.environ,
            {"REDIS_URL": "redis://redis.example:6379/2"},
        ):
            client = create_redis_client()

        self.assertIs(client, from_url.return_value)
        from_url.assert_called_once_with(
            "redis://redis.example:6379/2",
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        )

    def test_checks_connection_with_ping(self) -> None:
        client = Mock()

        check_redis_connection(client)

        client.ping.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
