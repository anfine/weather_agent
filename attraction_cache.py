import hashlib
import json
from typing import Any

from redis import Redis


CACHE_KEY_PREFIX = "weather_agent:attraction:v1"


def normalize_attraction_query(query: str) -> str:
    """统一景点查询词，避免大小写和首尾空格产生不同 key。"""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        raise ValueError("景点查询不能为空")
    return normalized_query


def build_attraction_cache_key(query: str) -> str:
    """根据用户实际查询词生成稳定的 Redis key。"""
    normalized_query = normalize_attraction_query(query)
    query_digest = hashlib.sha256(
        normalized_query.encode("utf-8")
    ).hexdigest()

    return f"{CACHE_KEY_PREFIX}:query:{query_digest}"


class AttractionCache:
    """读写序列化后的景点查询结果。"""

    def __init__(self, client: Redis) -> None:
        self._client = client

    def get(self, query: str) -> dict[str, Any] | None:
        """读取景点缓存；未命中或内容损坏时返回 None。"""
        key = build_attraction_cache_key(query)
        cached_value = self._client.get(key)

        if cached_value is None:
            return None

        try:
            payload = json.loads(cached_value)
        except json.JSONDecodeError:
            self._client.delete(key)
            return None

        if not isinstance(payload, dict):
            self._client.delete(key)
            return None

        status = payload.get("status")
        if status not in {"found", "not_found"}:
            self._client.delete(key)
            return None

        if status == "found" and not isinstance(
            payload.get("attraction"),
            dict,
        ):
            self._client.delete(key)
            return None

        return payload

    def _set(
        self,
        query: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> None:
        """序列化并写入带过期时间的缓存。"""
        if ttl_seconds <= 0:
            raise ValueError("缓存 TTL 必须大于 0")

        key = build_attraction_cache_key(query)
        serialized_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        self._client.set(
            key,
            serialized_payload,
            ex=ttl_seconds,
        )

    def set_found(
        self,
        query: str,
        attraction: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> None:
        """缓存成功查到的景点数据。"""
        self._set(
            query,
            {
                "status": "found",
                "attraction": attraction,
            },
            ttl_seconds=ttl_seconds,
        )

    def set_not_found(
        self,
        query: str,
        *,
        ttl_seconds: int,
    ) -> None:
        """短暂缓存数据库中不存在的查询词。"""
        self._set(
            query,
            {"status": "not_found"},
            ttl_seconds=ttl_seconds,
        )

    def clear_all(self, *, batch_size: int = 100) -> int:
        """分批删除当前版本的全部景点查询缓存。"""
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")

        pattern = f"{CACHE_KEY_PREFIX}:query:*"
        pending_keys: list[str] = []
        deleted_count = 0

        for key in self._client.scan_iter(
            match=pattern,
            count=batch_size,
        ):
            pending_keys.append(key)

            if len(pending_keys) >= batch_size:
                deleted_count += self._client.delete(*pending_keys)
                pending_keys.clear()

        if pending_keys:
            deleted_count += self._client.delete(*pending_keys)

        return deleted_count