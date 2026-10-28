# -*- coding: utf-8 -*-
"""app-window-2：API Key 不进 config.json；测试连接用临时参数，不改共享配置。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

from brain import Brain  # noqa: E402


class SecretKeyTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        shutil.copy(PET / "persona.md", self.dir)
        self.config = {"llm": {"enabled": False, "base_url": "https://api.deepseek.com/v1",
                               "model": "deepseek-chat", "api_key": "sk-old-in-config"},
                       "personal": {"enabled": False}}
        self.brain = Brain(self.dir, self.config, log=lambda *a: None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_api_key_moves_out_of_config(self):
        self.brain.save_api_key("sk-new-secret-123")
        self.assertNotIn("api_key", self.config["llm"])
        self.assertEqual((self.dir / ".secret_api_key").read_text(encoding="utf-8"), "sk-new-secret-123")
        self.assertEqual(self.brain._resolve_api_key()[0], "sk-new-secret-123")

    def test_test_connection_does_not_touch_config(self):
        before = {k: dict(v) if isinstance(v, dict) else v for k, v in self.config.items()}
        calls = []

        def fake_request(messages, options, key):
            calls.append((options["base_url"], options["model"], key))
            return "我在"

        self.brain._request_chat = fake_request
        ok, _detail = self.brain.test_connection({"api_key": "sk-typed-not-saved", "base_url": "https://x.example/v1",
                                                  "model": "m-test"})
        self.assertTrue(ok)
        self.assertEqual(calls[-1][1:], ("m-test", "sk-typed-not-saved"))
        self.assertIn("x.example", calls[-1][0])
        self.assertEqual(self.config, before)  # 共享配置一点没动


if __name__ == "__main__":
    unittest.main()
