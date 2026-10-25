# -*- coding: utf-8 -*-
"""DshStatusWatcher 的单元测试：只在本地临时目录里读写假状态文件，不联网。"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

from status_client import DshStatusWatcher  # noqa: E402


def write_status(path: Path, state: str = "idle", **extra) -> None:
    payload = {
        "ok": True,
        "state": state,
        "lastTool": None,
        "doneCount": 0,
        "errorCount": 0,
        "approvalOpen": False,
        "updatedAt": int(time.time() * 1000),
        "version": 1,
    }
    payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")


class DshStatusWatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "status.json"
        self.logs = []
        self.watcher = DshStatusWatcher(self.path, log=self.logs.append, stale_after_s=5)

    def test_missing_file_is_silent(self):
        self.assertIsNone(self.watcher.poll())
        self.assertFalse(self.watcher.connected)
        self.assertIsNone(self.watcher.poll())
        self.assertEqual(len(self.logs), 1)  # 只提示一次

    def test_connect_emits_once_then_quiet(self):
        write_status(self.path, "idle")
        event = self.watcher.poll()
        self.assertIsNotNone(event)
        self.assertTrue(event["connected"])
        self.assertEqual(event["state"], "idle")
        self.assertIsNone(self.watcher.poll())

    def test_state_transitions_emit_events(self):
        write_status(self.path, "running")
        event = self.watcher.poll()
        self.assertEqual(event["state"], "running")

        write_status(self.path, "tool", lastTool="bash", updatedAt=int(time.time() * 1000))
        event = self.watcher.poll()
        self.assertEqual(event["state"], "tool")
        self.assertEqual(event["lastTool"], "bash")

        write_status(self.path, "success", doneCount=1, lastTool="bash",
                     updatedAt=int(time.time() * 1000))
        event = self.watcher.poll()
        self.assertEqual(event["state"], "success")
        self.assertEqual(event["doneCount"], 1)

        write_status(self.path, "failure", doneCount=1, errorCount=1,
                     updatedAt=int(time.time() * 1000))
        event = self.watcher.poll()
        self.assertEqual(event["state"], "failure")
        self.assertEqual(event["errorCount"], 1)

    def test_same_state_but_new_done_count_emits(self):
        write_status(self.path, "success", doneCount=1)
        self.watcher.poll()
        write_status(self.path, "success", doneCount=2)
        event = self.watcher.poll()
        self.assertIsNotNone(event)
        self.assertEqual(event["doneCount"], 2)

    def test_disconnect_when_file_removed(self):
        write_status(self.path, "running")
        self.watcher.poll()
        self.path.unlink()
        event = self.watcher.poll()
        self.assertIsNotNone(event)
        self.assertEqual(event["state"], "disconnected")
        self.assertFalse(event["connected"])
        self.assertEqual(self.watcher.state, "idle")

    def test_stale_heartbeat_disconnects(self):
        write_status(self.path, "running", updatedAt=int((time.time() - 60) * 1000))
        event = self.watcher.poll()
        self.assertIsNone(event)  # 从没连上，不触发
        write_status(self.path, "running")
        self.watcher.poll()
        write_status(self.path, "running", updatedAt=int((time.time() - 60) * 1000))
        event = self.watcher.poll()
        self.assertIsNotNone(event)
        self.assertEqual(event["state"], "disconnected")

    def test_iso_timestamp_supported(self):
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_status(self.path, "running", updatedAt=iso)
        event = self.watcher.poll()
        self.assertIsNotNone(event)
        self.assertTrue(event["connected"])

    def test_invalid_state_ignored(self):
        write_status(self.path, "hacking")
        self.assertIsNone(self.watcher.poll())
        self.assertFalse(self.watcher.connected)

    def test_corrupt_json_does_not_crash(self):
        write_status(self.path, "running")
        self.watcher.poll()
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.watcher.poll())
        self.assertTrue(self.watcher.connected)  # 坏文件不主动断开，等下次心跳/文件恢复

    def test_snapshot_and_describe(self):
        write_status(self.path, "approval", approvalOpen=True)
        self.watcher.poll()
        snap = self.watcher.snapshot()
        self.assertTrue(snap["approvalOpen"])
        self.assertIn("审批", self.watcher.describe())


if __name__ == "__main__":
    unittest.main()
