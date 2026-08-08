import os
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import ceil
from threading import RLock
from time import monotonic
from typing import Protocol
from uuid import uuid4

from flask import Blueprint, Flask, current_app, jsonify, render_template, request
from markdown_it import MarkdownIt

from main import invoke_agent_turn, needs_city_follow_up


MAX_MESSAGE_LENGTH = 2000
MAX_SESSION_ID_LENGTH = 128
DEFAULT_MAX_SESSIONS = 200
DEFAULT_MAX_SESSION_TURNS = 6
DEFAULT_SESSION_TTL_SECONDS = 30 * 60
DEFAULT_RATE_LIMIT_REQUESTS = 10
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 3 * 60 * 60
DEFAULT_RATE_LIMIT_CLIENTS = 10_000
AgentTurnHandler = Callable[[list, str], dict]
MARKDOWN_RENDERER = MarkdownIt("js-default", {"breaks": True}).disable("image")


class SessionStore(Protocol):
    """Flask 对会话存储的最小依赖接口。"""

    def get(self, session_id: str) -> list: ...

    def save(self, session_id: str, messages: list) -> None: ...

    def delete(self, session_id: str) -> bool: ...


class RateLimiter(Protocol):
    """Flask API 使用的匿名请求限流接口。"""

    def check(self, client_id: str) -> "RateLimitDecision": ...


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int = 0


class InMemoryRateLimiter:
    """按客户端标识执行有界、线程安全的滑动窗口限流。"""

    def __init__(
        self,
        *,
        max_requests: int = DEFAULT_RATE_LIMIT_REQUESTS,
        window_seconds: float = DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
        max_clients: int = DEFAULT_RATE_LIMIT_CLIENTS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_requests <= 0 or window_seconds <= 0 or max_clients <= 0:
            raise ValueError("请求数、时间窗口和客户端数量必须大于 0")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_clients = max_clients
        self._clock = clock
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = RLock()

    def check(self, client_id: str) -> RateLimitDecision:
        with self._lock:
            now = self._clock()
            self._delete_inactive_clients(now)
            timestamps = self._requests.setdefault(client_id, deque())
            while timestamps and now - timestamps[0] >= self._window_seconds:
                timestamps.popleft()

            if len(timestamps) >= self._max_requests:
                retry_after = max(
                    1,
                    ceil(self._window_seconds - (now - timestamps[0])),
                )
                return RateLimitDecision(False, 0, retry_after)

            timestamps.append(now)
            self._requests.move_to_end(client_id)
            while len(self._requests) > self._max_clients:
                self._requests.popitem(last=False)
            return RateLimitDecision(
                True,
                self._max_requests - len(timestamps),
            )

    def _delete_inactive_clients(self, now: float) -> None:
        while self._requests:
            _, timestamps = next(iter(self._requests.items()))
            if timestamps and now - timestamps[-1] < self._window_seconds:
                break
            self._requests.popitem(last=False)


@dataclass
class _SessionEntry:
    messages: list
    last_accessed_at: float


class InMemorySessionStore:
    """有界进程内会话；按空闲时间过期并淘汰最久未使用项。"""

    def __init__(
        self,
        *,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_turns: int = DEFAULT_MAX_SESSION_TURNS,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_sessions <= 0 or max_turns <= 0 or ttl_seconds <= 0:
            raise ValueError("会话数量、轮数和过期时间必须大于 0")
        self._max_sessions = max_sessions
        self._max_turns = max_turns
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: OrderedDict[str, _SessionEntry] = OrderedDict()
        self._lock = RLock()

    def get(self, session_id: str) -> list:
        with self._lock:
            now = self._clock()
            self._delete_expired(now)
            entry = self._sessions.get(session_id)
            if entry is None:
                return []
            entry.last_accessed_at = now
            self._sessions.move_to_end(session_id)
            return list(entry.messages)

    def save(self, session_id: str, messages: list) -> None:
        with self._lock:
            now = self._clock()
            self._delete_expired(now)
            self._sessions[session_id] = _SessionEntry(
                messages=self._keep_recent_turns(messages),
                last_accessed_at=now,
            )
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def _delete_expired(self, now: float) -> None:
        expired_ids = [
            session_id
            for session_id, entry in self._sessions.items()
            if now - entry.last_accessed_at >= self._ttl_seconds
        ]
        for session_id in expired_ids:
            del self._sessions[session_id]

    def _keep_recent_turns(self, messages: list) -> list:
        human_message_indexes = [
            index
            for index, message in enumerate(messages)
            if getattr(message, "type", None) == "human"
        ]
        if len(human_message_indexes) <= self._max_turns:
            return list(messages)
        return list(messages[human_message_indexes[-self._max_turns] :])


def _assistant_text(message) -> str:
    """将 LangChain 的最终消息转换为接口可直接展示的文本。"""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def _render_assistant_markdown(text: str) -> str:
    """安全渲染 Agent Markdown；原始 HTML 和外部图片保持禁用。"""
    return MARKDOWN_RENDERER.render(text)


def create_api_blueprint(
    turn_handler: AgentTurnHandler,
    session_store: SessionStore,
    rate_limiter: RateLimiter,
) -> Blueprint:
    """创建 API 蓝图；后续可原样迁移到独立 routes 包。"""
    api = Blueprint("api", __name__, url_prefix="/api")

    @api.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @api.post("/chat")
    def chat():
        client_id = request.remote_addr or "unknown"
        rate_limit = rate_limiter.check(client_id)
        if not rate_limit.allowed:
            response = jsonify(
                {
                    "error": "请求过于频繁，请稍后重试",
                    "retry_after_seconds": rate_limit.retry_after_seconds,
                }
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(
                rate_limit.retry_after_seconds
            )
            return response

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "请求体必须是 JSON 对象"}), 400

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return jsonify({"error": "message 必须是非空字符串"}), 400
        message = message.strip()
        if len(message) > MAX_MESSAGE_LENGTH:
            return jsonify(
                {"error": f"message 不能超过 {MAX_MESSAGE_LENGTH} 个字符"}
            ), 400

        session_id = payload.get("session_id") or uuid4().hex
        if not isinstance(session_id, str) or not session_id.strip():
            return jsonify({"error": "session_id 必须是非空字符串"}), 400
        session_id = session_id.strip()
        if len(session_id) > MAX_SESSION_ID_LENGTH:
            return jsonify(
                {"error": f"session_id 不能超过 {MAX_SESSION_ID_LENGTH} 个字符"}
            ), 400

        try:
            result = turn_handler(session_store.get(session_id), message)
            messages = result["messages"]
            if not messages:
                raise ValueError("Agent 未返回消息")
        except Exception:
            current_app.logger.exception("Agent request failed")
            return jsonify({"error": "天气服务暂时不可用，请稍后重试"}), 502

        session_store.save(session_id, messages)
        reply = _assistant_text(messages[-1])
        return jsonify(
            {
                "session_id": session_id,
                "reply": reply,
                "reply_html": _render_assistant_markdown(reply),
                "needs_follow_up": needs_city_follow_up(messages),
            }
        )

    @api.delete("/sessions/<session_id>")
    def delete_session(session_id: str):
        session_store.delete(session_id)
        return jsonify({"status": "deleted", "session_id": session_id})

    return api


def create_app(
    config: Mapping[str, object] | None = None,
    *,
    turn_handler: AgentTurnHandler = invoke_agent_turn,
    session_store: SessionStore | None = None,
    rate_limiter: RateLimiter | None = None,
) -> Flask:
    """Flask 应用工厂。"""
    app = Flask(__name__)
    app.config.from_mapping(
        RATE_LIMIT_REQUESTS=DEFAULT_RATE_LIMIT_REQUESTS,
        RATE_LIMIT_WINDOW_SECONDS=DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
    )
    if config:
        app.config.from_mapping(config)
    app.json.ensure_ascii = False

    store = session_store or InMemorySessionStore()
    limiter = rate_limiter or InMemoryRateLimiter(
        max_requests=int(app.config["RATE_LIMIT_REQUESTS"]),
        window_seconds=float(app.config["RATE_LIMIT_WINDOW_SECONDS"]),
    )
    app.register_blueprint(
        create_api_blueprint(turn_handler, store, limiter)
    )

    @app.get("/")
    def index():
        return render_template("index.html")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
