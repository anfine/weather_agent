import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


class DeploymentConfigTests(unittest.TestCase):
    def test_caddy_keeps_existing_site_and_adds_weather_site(self) -> None:
        caddyfile = (PROJECT_ROOT / "deploy" / "Caddyfile").read_text()

        self.assertIn("anfine.top {", caddyfile)
        self.assertIn("reverse_proxy 127.0.0.1:8080", caddyfile)
        self.assertIn("weather.anfine.top {", caddyfile)
        self.assertIn("reverse_proxy 127.0.0.1:8000", caddyfile)
        self.assertNotIn("127.0.0.1:5000", caddyfile)

    def test_systemd_uses_gunicorn_and_trusts_one_local_proxy(self) -> None:
        service = (
            PROJECT_ROOT / "deploy" / "weather-agent.service"
        ).read_text()

        self.assertIn("User=weather-agent", service)
        self.assertIn("WorkingDirectory=/opt/weather-agent", service)
        self.assertIn("Environment=TRUST_PROXY_HEADERS=1", service)
        self.assertIn(".venv/bin/gunicorn app:app", service)
        self.assertNotIn("python app.py", service)


if __name__ == "__main__":
    unittest.main()
