import os
import unittest

from langchain.messages import AIMessage, HumanMessage

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

from app import InMemorySessionStore, create_app


class FlaskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[list, str]] = []

        def fake_turn(messages: list, query: str) -> dict:
            self.calls.append((messages, query))
            return {
                "messages": [
                    *messages,
                    HumanMessage(content=query),
                    AIMessage(content=f"回答：{query}"),
                ]
            }

        flask_app = create_app(
            {"TESTING": True},
            turn_handler=fake_turn,
            session_store=InMemorySessionStore(),
        )
        self.client = flask_app.test_client()

    def test_health(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_chat_creates_session_and_returns_answer(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={"message": "明天适合去华山吗？"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["session_id"])
        self.assertEqual(payload["reply"], "回答：明天适合去华山吗？")
        self.assertFalse(payload["needs_follow_up"])

    def test_chat_response_keeps_chinese_characters(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={"message": "华山天气"},
        )

        self.assertIn("回答：华山天气".encode(), response.data)
        self.assertNotIn(b"\\u56de\\u7b54", response.data)

    def test_chat_reuses_session_context(self) -> None:
        first = self.client.post(
            "/api/chat",
            json={"message": "明天适合去老君山吗？"},
        ).get_json()
        self.client.post(
            "/api/chat",
            json={
                "message": "河南洛阳的",
                "session_id": first["session_id"],
            },
        )

        previous_messages, query = self.calls[1]
        self.assertEqual(query, "河南洛阳的")
        self.assertEqual(len(previous_messages), 2)
        self.assertEqual(previous_messages[0].content, "明天适合去老君山吗？")

    def test_chat_rejects_empty_message(self) -> None:
        response = self.client.post("/api/chat", json={"message": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.get_json()["error"])

    def test_delete_session_clears_context(self) -> None:
        first = self.client.post(
            "/api/chat",
            json={"message": "第一轮"},
        ).get_json()
        self.client.delete(f"/api/sessions/{first['session_id']}")
        self.client.post(
            "/api/chat",
            json={"message": "重新开始", "session_id": first["session_id"]},
        )

        self.assertEqual(self.calls[1][0], [])


if __name__ == "__main__":
    unittest.main()
