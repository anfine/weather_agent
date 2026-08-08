import os
import unittest

from langchain.messages import AIMessage, HumanMessage

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

from app import InMemoryRateLimiter, InMemorySessionStore, create_app
from database import DatabaseSchemaNotReadyError


class FlaskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[list, str]] = []

        def fake_turn(messages: list, query: str) -> dict:
            if query == "触发测试异常":
                raise RuntimeError("测试用内部错误")
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

    def test_ready_when_database_and_migrations_are_ready(self) -> None:
        ready_app = create_app(
            {"TESTING": True},
            turn_handler=lambda messages, query: {},
            readiness_checker=lambda: None,
        )

        response = ready_app.test_client().get("/api/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ready"})

    def test_ready_rejects_outdated_database_schema(self) -> None:
        def outdated_schema() -> None:
            raise DatabaseSchemaNotReadyError("内部 revision 信息")

        ready_app = create_app(
            {"TESTING": True},
            turn_handler=lambda messages, query: {},
            readiness_checker=outdated_schema,
        )

        with self.assertLogs("app", level="WARNING"):
            response = ready_app.test_client().get("/api/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {
                "status": "not_ready",
                "reason": "database_schema_outdated",
            },
        )
        self.assertNotIn(b"revision", response.data)

    def test_ready_hides_database_connection_error(self) -> None:
        def unavailable_database() -> None:
            raise RuntimeError("mysql://user:secret@example.invalid")

        ready_app = create_app(
            {"TESTING": True},
            turn_handler=lambda messages, query: {},
            readiness_checker=unavailable_database,
        )

        with self.assertLogs("app", level="ERROR"):
            response = ready_app.test_client().get("/api/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {
                "status": "not_ready",
                "reason": "database_unavailable",
            },
        )
        self.assertNotIn(b"secret", response.data)

    def test_index_renders_chat_page(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("出行天气参谋".encode(), response.data)
        self.assertIn(b'id="chat-form"', response.data)
        self.assertIn(b'/static/css/app.css', response.data)
        self.assertIn(b'/static/js/app.js', response.data)

        for asset_path in ("/static/css/app.css", "/static/js/app.js"):
            with self.subTest(asset_path=asset_path):
                with self.client.get(asset_path) as asset_response:
                    self.assertEqual(asset_response.status_code, 200)

    def test_chat_creates_session_and_returns_answer(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={"message": "明天适合去华山吗？"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["session_id"])
        self.assertEqual(payload["reply"], "回答：明天适合去华山吗？")
        self.assertEqual(payload["reply_html"], "<p>回答：明天适合去华山吗？</p>\n")
        self.assertFalse(payload["needs_follow_up"])

    def test_chat_renders_safe_markdown_table(self) -> None:
        markdown = (
            "**各日评分**\n\n"
            "| 日期 | 分数 |\n"
            "|---|---:|\n"
            "| 8/07 | 57.0 |\n\n"
            "<script>alert('xss')</script>"
        )

        payload = self.client.post(
            "/api/chat",
            json={"message": markdown},
        ).get_json()

        self.assertIn("<strong>各日评分</strong>", payload["reply_html"])
        self.assertIn("<table>", payload["reply_html"])
        self.assertIn("&lt;script&gt;", payload["reply_html"])
        self.assertNotIn("<script>", payload["reply_html"])

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

    def test_chat_hides_internal_agent_error(self) -> None:
        with self.assertLogs("app", level="ERROR"):
            response = self.client.post(
                "/api/chat",
                json={"message": "触发测试异常"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json(),
            {"error": "天气服务暂时不可用，请稍后重试"},
        )
        self.assertNotIn("测试用内部错误".encode(), response.data)

    def test_rate_limit_counts_invalid_requests_and_skips_agent(self) -> None:
        limited_app = create_app(
            {"TESTING": True},
            turn_handler=lambda messages, query: self.fail(
                "达到限流前不应调用 Agent"
            ),
            session_store=InMemorySessionStore(),
            rate_limiter=InMemoryRateLimiter(
                max_requests=2,
                window_seconds=3 * 60 * 60,
            ),
        )
        client = limited_app.test_client()

        self.assertEqual(client.post("/api/chat").status_code, 400)
        self.assertEqual(
            client.post("/api/chat", json={"message": "  "}).status_code,
            400,
        )
        response = client.post("/api/chat", json={"message": "华山"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "10800")
        self.assertEqual(response.get_json()["retry_after_seconds"], 10800)

    def test_rate_limit_counts_agent_failures(self) -> None:
        limited_app = create_app(
            {"TESTING": True},
            turn_handler=lambda messages, query: (_ for _ in ()).throw(
                RuntimeError("测试异常")
            ),
            session_store=InMemorySessionStore(),
            rate_limiter=InMemoryRateLimiter(
                max_requests=1,
                window_seconds=3 * 60 * 60,
            ),
        )
        client = limited_app.test_client()

        with self.assertLogs("app", level="ERROR"):
            first_response = client.post(
                "/api/chat", json={"message": "第一次"}
            )
        second_response = client.post(
            "/api/chat", json={"message": "第二次"}
        )

        self.assertEqual(first_response.status_code, 502)
        self.assertEqual(second_response.status_code, 429)

    def test_trusted_proxy_uses_forwarded_client_ip_for_rate_limit(self) -> None:
        def fake_turn(messages: list, query: str) -> dict:
            return {
                "messages": [
                    HumanMessage(content=query),
                    AIMessage(content="回答"),
                ]
            }

        proxied_app = create_app(
            {"TESTING": True, "TRUST_PROXY_HEADERS": True},
            turn_handler=fake_turn,
            rate_limiter=InMemoryRateLimiter(
                max_requests=1,
                window_seconds=3 * 60 * 60,
            ),
        )
        client = proxied_app.test_client()

        first = client.post(
            "/api/chat",
            json={"message": "第一位用户"},
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        second = client.post(
            "/api/chat",
            json={"message": "第二位用户"},
            headers={"X-Forwarded-For": "203.0.113.11"},
        )
        repeated = client.post(
            "/api/chat",
            json={"message": "第一位用户再次请求"},
            headers={"X-Forwarded-For": "203.0.113.10"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(repeated.status_code, 429)

    def test_forwarded_client_ip_is_ignored_without_proxy_trust(self) -> None:
        def fake_turn(messages: list, query: str) -> dict:
            return {
                "messages": [
                    HumanMessage(content=query),
                    AIMessage(content="回答"),
                ]
            }

        direct_app = create_app(
            {"TESTING": True, "TRUST_PROXY_HEADERS": False},
            turn_handler=fake_turn,
            rate_limiter=InMemoryRateLimiter(
                max_requests=1,
                window_seconds=3 * 60 * 60,
            ),
        )
        client = direct_app.test_client()

        first = client.post(
            "/api/chat",
            json={"message": "第一次"},
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        second = client.post(
            "/api/chat",
            json={"message": "第二次"},
            headers={"X-Forwarded-For": "203.0.113.11"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_delete_session_clears_context(self) -> None:
        first = self.client.post(
            "/api/chat",
            json={"message": "第一轮"},
        ).get_json()
        delete_response = self.client.delete(
            f"/api/sessions/{first['session_id']}"
        )
        self.client.post(
            "/api/chat",
            json={"message": "重新开始", "session_id": first["session_id"]},
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.get_json()["status"], "deleted")
        self.assertEqual(self.calls[1][0], [])


class InMemorySessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 0.0
        self.store = InMemorySessionStore(
            max_sessions=2,
            max_turns=2,
            ttl_seconds=30,
            clock=lambda: self.now,
        )

    @staticmethod
    def messages_for(*queries: str) -> list:
        messages = []
        for query in queries:
            messages.extend(
                [
                    HumanMessage(content=query),
                    AIMessage(content=f"回答：{query}"),
                ]
            )
        return messages

    def test_evicts_least_recently_used_session(self) -> None:
        self.store.save("old-active", self.messages_for("第一问"))
        self.now = 1
        self.store.save("new-idle", self.messages_for("第二问"))
        self.now = 2
        self.store.get("old-active")
        self.now = 3
        self.store.save("new", self.messages_for("第三问"))

        self.assertEqual(self.store.get("new-idle"), [])
        self.assertTrue(self.store.get("old-active"))
        self.assertTrue(self.store.get("new"))

    def test_access_refreshes_session_expiration(self) -> None:
        self.store.save("active", self.messages_for("第一问"))
        self.now = 20
        self.assertTrue(self.store.get("active"))
        self.now = 49
        self.assertTrue(self.store.get("active"))
        self.now = 80

        self.assertEqual(self.store.get("active"), [])

    def test_keeps_only_most_recent_turns(self) -> None:
        self.store.save(
            "conversation",
            self.messages_for("第一问", "第二问", "第三问", "第四问"),
        )

        messages = self.store.get("conversation")

        self.assertEqual(len(messages), 4)
        self.assertEqual(
            [message.content for message in messages if message.type == "human"],
            ["第三问", "第四问"],
        )


class InMemoryRateLimiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 0.0
        self.limiter = InMemoryRateLimiter(
            max_requests=2,
            window_seconds=30,
            max_clients=2,
            clock=lambda: self.now,
        )

    def test_blocks_request_until_oldest_entry_leaves_window(self) -> None:
        self.assertTrue(self.limiter.check("client-a").allowed)
        self.now = 10
        self.assertTrue(self.limiter.check("client-a").allowed)
        self.now = 20

        blocked = self.limiter.check("client-a")

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after_seconds, 10)
        self.now = 30
        self.assertTrue(self.limiter.check("client-a").allowed)

    def test_limits_number_of_tracked_clients(self) -> None:
        self.limiter.check("old-client")
        self.limiter.check("second-client")
        self.limiter.check("new-client")

        decision = self.limiter.check("old-client")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.remaining, 1)


if __name__ == "__main__":
    unittest.main()
