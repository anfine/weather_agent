from dataclasses import dataclass
from typing import Protocol


class RateLimiter(Protocol):
    """Flask API 使用的匿名请求限流接口。"""

    def check(self, client_id: str) -> "RateLimitDecision": ...


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int = 0
