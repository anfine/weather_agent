import os

from dotenv import load_dotenv
from redis import Redis


load_dotenv()


DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
REDIS_SOCKET_TIMEOUT_SECONDS = 1


def create_redis_client(redis_url: str | None = None) -> Redis:
    """创建可在线程间复用、带短超时的 Redis 客户端。"""
    url = redis_url or os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
    )


redis_client = create_redis_client()


def check_redis_connection(client: Redis | None = None) -> None:
    """执行轻量 Redis 连通检查。"""
    selected_client = redis_client if client is None else client
    selected_client.ping()
