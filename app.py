import os
from collections.abc import Mapping
from threading import RLock
from typing import Callable, Protocol
from uuid import uuid4

from flask import Blueprint, Flask, current_app, jsonify, request

from main import invoke_agent_turn, needs_city_follow_up


MAX_MESSAGE_LENGTH = 2000
MAX_SESSION_ID_LENGTH = 128
AgentTurnHandler = Callable[[list, str], dict]


class SessionStore(Protocol):
    """Flask 对会话存储的最小依赖接口。"""

    def get(self, session_id: str) -> list: ...

    def save(self, session_id: str, messages: list) -> None: ...

    def delete(self, session_id: str) -> bool: ...


class InMemorySessionStore:
    """MVP 会话存储；服务重启后自动清空。"""

    def __init__(self) -> None:
        self._messages: dict[str, list] = {}
        self._lock = RLock()

    def get(self, session_id: str) -> list:
        with self._lock:
            return list(self._messages.get(session_id, []))

    def save(self, session_id: str, messages: list) -> None:
        with self._lock:
            self._messages[session_id] = list(messages)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._messages.pop(session_id, None) is not None


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


def create_api_blueprint(
    turn_handler: AgentTurnHandler,
    session_store: SessionStore,
) -> Blueprint:
    """创建 API 蓝图；后续可原样迁移到独立 routes 包。"""
    api = Blueprint("api", __name__, url_prefix="/api")

    @api.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @api.post("/chat")
    def chat():
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
        return jsonify(
            {
                "session_id": session_id,
                "reply": _assistant_text(messages[-1]),
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
) -> Flask:
    """Flask 应用工厂。"""
    app = Flask(__name__)
    if config:
        app.config.from_mapping(config)
    app.json.ensure_ascii = False

    store = session_store or InMemorySessionStore()
    app.register_blueprint(create_api_blueprint(turn_handler, store))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
