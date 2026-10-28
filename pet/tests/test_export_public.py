# -*- coding: utf-8 -*-
"""repo-1：导出目标目录保护——参数给错不会清空别的目录。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

try:  # 导出脚本只在私有库里有；公开库里跳过这组测试
    import export_public  # noqa: E402
except ImportError:
    export_public = None


@unittest.skipIf(export_public is None, "公开库里没有 tools/export_public.py")
class ExportTargetTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.root = self.base / "workspace" / "private"
        self.root.mkdir(parents=True)
        self.default = self.base / "workspace" / "dfy-desktop-pet"

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def check(self, target):
        return export_public.check_target(target, self.root, self.default)

    def test_parent_and_inside_refused(self):
        self.assertTrue(self.check(self.root.parent))
        self.assertTrue(self.check(self.root))
        self.assertTrue(self.check(self.root / "sub"))

    def test_non_empty_without_marker_refused_and_untouched(self):
        other = self.base / "workspace" / "important"
        other.mkdir()
        (other / "keep.txt").write_text("别删我", encoding="utf-8")
        self.assertIn("不是空的", self.check(other))
        self.assertTrue((other / "keep.txt").exists())

    def test_marked_dir_and_default_allowed(self):
        marked = self.base / "workspace" / "export-copy"
        marked.mkdir()
        (marked / export_public.MARKER).write_text("x", encoding="utf-8")
        (marked / "old.py").write_text("x", encoding="utf-8")
        self.assertEqual(self.check(marked), "")
        self.assertEqual(self.check(self.default), "")

    def test_empty_non_default_refused(self):
        empty = self.base / "workspace" / "empty"
        empty.mkdir()
        self.assertTrue(self.check(empty))


if __name__ == "__main__":
    unittest.main()
