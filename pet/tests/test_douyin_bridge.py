# -*- coding: utf-8 -*-
"""抖音助手桥测试：临时目录，不读写真实登录/私信。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from douyin_bridge import DouyinBridge, GEN_START, SYNC_START


class DouyinBridgeTest(unittest.TestCase):
    def test_report_summary_and_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "douyin-browser"
            persona_dir = root / "memory" / "persona"
            reports = root / "memory" / "reports" / "videos"
            persona_dir.mkdir(parents=True)
            reports.mkdir(parents=True)
            (persona_dir / "role-card.md").write_text(
                "# 小鲸的人格卡\n\n" + GEN_START + "\n生成区\n<!-- X:END -->\n", encoding="utf-8")
            (persona_dir / "state.json").write_text('{"interests": [{"a": 1}]}', encoding="utf-8")
            (reports / "2026-10-26.md").write_text(
                "# 大肥鱼刷视频日报 · 2026-10-26\n\n记录涉及 3 条作品，观察 5 次。\n",
                encoding="utf-8")
            bridge = DouyinBridge(root=root, log=lambda *a: None)
            self.assertIn("3 条作品", bridge.report_summary())
            self.assertIn("抖音助手", bridge.status_text())
            persona = Path(tmp) / "persona.md"
            persona.write_text("# 小鲸鱼\n\n你是小鲸鱼，本鲸。", encoding="utf-8")
            ok, _ = bridge.sync_persona(persona)
            self.assertTrue(ok)
            text = (persona_dir / "role-card.md").read_text(encoding="utf-8")
            self.assertIn(SYNC_START, text)
            self.assertIn("小鲸鱼", text)
            self.assertIn(GEN_START, text)

    def test_missing_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = DouyinBridge(root=Path(tmp) / "none", log=lambda *a: None)
            self.assertFalse(bridge.available)
            self.assertIsNone(bridge.latest_report())
            self.assertEqual(bridge.report_summary(), "")


if __name__ == "__main__":
    unittest.main()
