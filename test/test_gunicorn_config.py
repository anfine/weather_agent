import unittest
from pathlib import Path
from runpy import run_path


class GunicornConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config_path = Path(__file__).parents[1] / "gunicorn.conf.py"
        cls.config = run_path(str(config_path))

    def test_uses_single_threaded_worker_process(self) -> None:
        self.assertEqual(self.config["bind"], "127.0.0.1:8000")
        self.assertEqual(self.config["workers"], 1)
        self.assertEqual(self.config["worker_class"], "gthread")
        self.assertEqual(self.config["threads"], 4)

    def test_allows_slow_agent_requests(self) -> None:
        self.assertEqual(self.config["timeout"], 120)
        self.assertEqual(self.config["graceful_timeout"], 30)


if __name__ == "__main__":
    unittest.main()
