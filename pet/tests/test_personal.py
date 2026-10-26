# -*- coding: utf-8 -*-
"""PersonalLibrary 单元测试：全部在 tempfile 假个人库里跑，不碰真实个人库、不联网。

覆盖：缺文件优雅降级、字段提炼、prompt 截断、技术坑过滤、quick_recap 长度、
find 检索、summary 不泄漏正文、mtime 重载、关闭开关、坏 JSON、只读不写盘、日志不打印正文。
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

from personal import PersonalLibrary  # noqa: E402

PROFILE_TEXT = """# 测试用户背景

- Test User。Sample University Electrical Engineering 硕士在读，预计 2027 年毕业。
- 目标是美国有全额资助的 PhD。更偏好构建 LLM Agent 与科研 Harness。
- 本科背景包括示例大学，2021—2025。

## 怎样合作最有效

- 用自然中文和一个贯穿例子讲清楚。
- 修改后说明实际结果与未测范围。
- 不要重复确认常规操作。

## 需要更多背景时读原件

- 不要把旧招生网页升级为个人成果。
"""

BACKGROUND_TEXT = """# 我的背景与合作偏好

- 我是 Test User，Sample University 硕士在读。
- 喜欢构建 Agent 和科研工具。
"""


def default_journal() -> list[dict]:
    return [
        {"seq": 1, "ts": "2026-10-20T10:00:00", "by": "user", "topic": "preference",
         "text": "用户偏好：回答先给结果，少套话。"},
        {"seq": 2, "ts": "2026-10-21T10:00:00", "by": "user", "topic": "pitfall",
         "text": "PITFALL_MARKER：这是技术坑细节，绝不该进人格提示。"},
        {"seq": 3, "ts": "2026-10-22T10:00:00", "by": "user", "topic": "preference",
         "text": "用户偏好：用简体中文和具体例子。"},
        {"seq": 4, "ts": "2026-10-23T10:00:00", "by": "user", "topic": "preference",
         "text": "用户偏好：先把方案写清楚再执行。"},
        {"seq": 5, "ts": "2026-10-24T10:00:00", "by": "user", "topic": "preference",
         "text": "用户偏好：报告里保留未验证点。"},
    ]


def write_library(root: Path, *, profile: str | None = PROFILE_TEXT,
                  background: str | None = BACKGROUND_TEXT,
                  journal: list[dict] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if profile is not None:
        (root / "context").mkdir(parents=True, exist_ok=True)
        (root / "context" / "profile.md").write_text(profile, encoding="utf-8")
    if background is not None:
        (root / "给Claude导入的个人背景-精简版.md").write_text(background, encoding="utf-8")
    (root / "preferences.json").write_text(
        json.dumps({"language": "简体中文", "reasoning_effort": "medium",
                    "nested": {"level": 3}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "memory").mkdir(parents=True, exist_ok=True)
    entries = journal if journal is not None else default_journal()
    (root / "memory" / "journal.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in entries) + "\n",
        encoding="utf-8",
    )
    return root


def snapshot_tree(root: Path) -> dict:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class PersonalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_root = Path(self.tmp.name)
        self.logs: list[str] = []

    def make(self, **kwargs) -> PersonalLibrary:
        root = write_library(self.tmp_root / "Shared", **kwargs)
        return PersonalLibrary(path=root, log=self.logs.append)

    # ---------- 缺文件 / 关闭 ----------
    def test_missing_library_degrades_without_crash(self):
        library = PersonalLibrary(path=self.tmp_root / "nope", log=self.logs.append)
        self.assertFalse(library.is_available)
        self.assertIsNone(library.loaded_at)
        self.assertIn("没找到个人库目录", library.error or "")
        self.assertEqual(library.prompt_block(), "")
        self.assertEqual(library.quick_recap(), "")
        self.assertEqual(library.find("PhD"), [])
        self.assertIn("未找到", library.summary())
        self.assertFalse(library.reload())  # signature 没变就不再读
        self.assertTrue(library.reload(force=True))  # force 一定重读
        self.assertFalse(library.is_available)

    def test_disabled_library_does_nothing(self):
        root = write_library(self.tmp_root / "Shared")
        library = PersonalLibrary(path=root, enabled=False, log=self.logs.append)
        self.assertFalse(library.is_available)
        self.assertEqual(library.prompt_block(), "")
        self.assertEqual(library.find("Test"), [])
        self.assertIn("已关闭", library.summary())
        self.assertIn("已关闭", library.error or "")

    def test_only_journal_partial_library_still_works(self):
        library = self.make(profile=None, background=None)
        # profile/背景缺失但 preferences + journal 在，仍应可用
        self.assertTrue(library.is_available)
        self.assertIn("profile.md", library.error or "")
        block = library.prompt_block()
        self.assertTrue(block.startswith("【用户个人档案"))
        self.assertIn("最近偏好记忆", block)

    def test_corrupt_journal_line_is_skipped(self):
        root = write_library(self.tmp_root / "Shared")
        journal_path = root / "memory" / "journal.jsonl"
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        library = PersonalLibrary(path=root, log=self.logs.append)
        self.assertTrue(library.is_available)
        self.assertEqual(len(library.journal_entries), 5)
        self.assertIn("不是合法 JSON", library.error or "")

    # ---------- 加载与字段 ----------
    def test_loads_and_extracts_profile_fields(self):
        library = self.make()
        self.assertTrue(library.is_available)
        self.assertIsNotNone(library.loaded_at)
        self.assertEqual(library.fields.get("name"), "Test User")
        self.assertEqual(library.fields.get("school"), "Sample University")
        self.assertIn("PhD", library.fields.get("goal", ""))
        self.assertIn("LLM Agent", library.fields.get("direction", ""))
        self.assertGreaterEqual(len(library.fields.get("collaboration") or []), 2)

    def test_prompt_block_contents_and_no_pitfall(self):
        library = self.make()
        block = library.prompt_block()
        self.assertLessEqual(len(block), library.max_chars)
        for expected in ("姓名：Test User", "Sample University", "PhD", "LLM Agent", "合作偏好"):
            self.assertIn(expected, block)
        self.assertIn("简体中文", block)
        # 最近 3 条 preference 记忆在里面，更早的不塞；技术坑绝不进提示
        self.assertIn("报告里保留未验证点", block)
        self.assertIn("先把方案写清楚", block)
        self.assertIn("用简体中文和具体例子", block)
        self.assertNotIn("回答先给结果", block)  # 最早的偏好被最近 3 条挤掉
        self.assertNotIn("PITFALL_MARKER", block)
        self.assertNotIn("技术坑", block)
        # 明确要求模型不要逐字复述 / 不要编造
        self.assertIn("不要逐字复述", block)
        self.assertIn("不要编造", block)

    def test_prompt_block_keeps_only_three_recent_preferences(self):
        entries = default_journal()
        # seq 1 是最早的 preference，应被最近 3 条挤掉
        library = self.make(journal=entries)
        block = library.prompt_block()
        self.assertNotIn("回答先给结果", block)
        self.assertIn("用简体中文和具体例子", block)

    def test_prompt_block_truncates_to_max_chars(self):
        root = write_library(self.tmp_root / "Shared")
        library = PersonalLibrary(path=root, max_chars=260, log=self.logs.append)
        block = library.prompt_block()
        self.assertLessEqual(len(block), 260)
        self.assertIn("不要逐字复述", block)  # 截断也不丢安全尾巴
        self.assertGreater(len(block), 20)

    def test_prompt_block_with_query_adds_related(self):
        library = self.make()
        block = library.prompt_block(query="PhD")
        self.assertIn("和当前话题相关", block)
        self.assertIn("PhD", block)

    # ---------- quick_recap ----------
    def test_quick_recap_length_and_content(self):
        library = self.make()
        recap = library.quick_recap()
        self.assertTrue(recap.startswith("我记得你："))
        self.assertLessEqual(len(recap), 80)
        self.assertIn("Test User", recap)
        self.assertIn("Sample University", recap)

    def test_quick_recap_short_for_long_fields(self):
        long_profile = (
            "- Test Verylongname Person。Sample University of Long Names "
            "Electrical Engineering 硕士在读。\n"
            "- 目标是美国有全额资助的 PhD 并且方向是构建 LLM Agent 与科研 Harness。\n"
        )
        library = self.make(profile=long_profile)
        recap = library.quick_recap()
        self.assertLessEqual(len(recap), 80)

    # ---------- find ----------
    def test_find_returns_relevant_entries(self):
        library = self.make()
        hits = library.find("PhD", limit=5)
        self.assertTrue(hits)
        self.assertLessEqual(len(hits), 5)
        self.assertTrue(any("PhD" in item["text"] for item in hits))
        for item in hits:
            self.assertIn("source", item)
            self.assertIn(item["source"], {"profile", "background", "preferences", "journal"})

        pref_hits = library.find("简体中文")
        self.assertTrue(pref_hits)
        self.assertTrue(any(item["source"] in ("preferences", "journal") for item in pref_hits))

    def test_find_excludes_pitfall_and_respects_limit(self):
        library = self.make()
        self.assertEqual(library.find("PITFALL_MARKER"), [])
        self.assertEqual(library.find("技术坑"), [])
        hits = library.find("用户", limit=2)
        self.assertLessEqual(len(hits), 2)
        self.assertTrue(all("PITFALL_MARKER" not in item["text"] for item in hits))

    def test_find_empty_query_returns_general_material(self):
        library = self.make()
        hits = library.find("", limit=4)
        self.assertEqual(len(hits), 4)
        texts = " ".join(item["text"] for item in hits)
        self.assertIn("Test User", texts)

    # ---------- summary ----------
    def test_summary_has_counts_without_body(self):
        library = self.make()
        summary = library.summary()
        self.assertIn("已加载", summary)
        self.assertIn("偏好 3 项", summary)
        self.assertIn("记忆 5 条", summary)
        self.assertIn("偏好 4，其他 1", summary)
        self.assertIn(str(self.tmp_root), summary)
        # 不能带正文（偏好原文 / 技术坑原文）
        self.assertNotIn("回答先给结果", summary)
        self.assertNotIn("PITFALL_MARKER", summary)
        self.assertNotIn("Test User", summary)

    # ---------- 重载 ----------
    def test_reload_detects_mtime_change_only_when_needed(self):
        root = write_library(self.tmp_root / "Shared")
        library = PersonalLibrary(path=root, log=self.logs.append)
        self.assertFalse(library.reload())  # 没变化 → 不重读
        journal_path = root / "memory" / "journal.jsonl"
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"seq": 9, "ts": "2026-10-25T10:00:00", "by": "user",
                                     "topic": "preference", "text": "用户偏好：新偏好ALPHA。"},
                                    ensure_ascii=False) + "\n")
        self.assertTrue(library.reload())
        self.assertIn("新偏好ALPHA", library.prompt_block())
        self.assertTrue(library.reload(force=True))
        self.assertIn("新偏好ALPHA", library.find("ALPHA")[0]["text"])

    def test_summary_after_reload_loaded_at_present(self):
        library = self.make()
        self.assertRegex(library.loaded_at or "", r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    # ---------- 安全：只读、日志不打印正文 ----------
    def test_never_writes_personal_library(self):
        root = write_library(self.tmp_root / "Shared")
        before = snapshot_tree(root)
        library = PersonalLibrary(path=root, log=self.logs.append)
        library.reload(force=True)
        library.prompt_block("PhD")
        library.quick_recap()
        library.find("中文")
        library.summary()
        after = snapshot_tree(root)
        self.assertEqual(before, after)

    def test_logs_never_contain_personal_body(self):
        root = write_library(self.tmp_root / "Shared")
        library = PersonalLibrary(path=root, log=self.logs.append)
        library.reload(force=True)
        library.prompt_block()
        library.find("中文")
        joined = "\n".join(self.logs)
        self.assertIn("个人库：已加载", joined)
        self.assertNotIn("PITFALL_MARKER", joined)
        self.assertNotIn("回答先给结果", joined)
        self.assertNotIn("Test User", joined)

    def test_log_callback_failure_does_not_break_load(self):
        root = write_library(self.tmp_root / "Shared")

        def bad_log(_message):
            raise RuntimeError("log 坏了")

        library = PersonalLibrary(path=root, log=bad_log)
        self.assertTrue(library.is_available)
        self.assertTrue(library.prompt_block())


if __name__ == "__main__":
    unittest.main()
