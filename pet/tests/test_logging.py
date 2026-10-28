# -*- coding: utf-8 -*-
"""日志：按大小轮转；没人接的异常（界面回调、线程）也写进日志。"""
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


class LoggingTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._old = (app.LOG_DIR, app.LOG_MAX_BYTES, threading.excepthook)
        app.LOG_DIR = self.dir
        app.LOG_MAX_BYTES = 200

    def tearDown(self):
        app.LOG_DIR, app.LOG_MAX_BYTES, threading.excepthook = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_rotates_and_keeps_two_old(self):
        for i in range(40):
            app.log(f"第 {i} 行 " + "x" * 20)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ["app.log", "app.log.1", "app.log.2"])
        self.assertLess((self.dir / "app.log").stat().st_size, 400)
        self.assertIn("第 39 行", (self.dir / "app.log").read_text(encoding="utf-8"))

    def test_thread_exception_logged(self):
        class FakeRoot:
            pass

        old_sys = sys.excepthook
        try:
            app.install_exception_logging(FakeRoot())
            worker = threading.Thread(target=lambda: 1 / 0, name="坏线程")
            worker.start()
            worker.join()
        finally:
            sys.excepthook = old_sys
        text = (self.dir / "app.log").read_text(encoding="utf-8")
        self.assertIn("坏线程", text)
        self.assertIn("ZeroDivisionError", text)


if __name__ == "__main__":
    unittest.main()
