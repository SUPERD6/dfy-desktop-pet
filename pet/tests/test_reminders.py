# -*- coding: utf-8 -*-
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reminders import ASK_RE, ReminderBook, cn_number, event_name, parse_when  # noqa: E402

NOW = datetime(2026, 10, 25, 16, 0)  # 周日下午 4 点


class ParseTest(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(cn_number("十二"), 12)
        self.assertEqual(cn_number("二十五"), 25)
        self.assertEqual(cn_number("两"), 2)
        self.assertEqual(cn_number("8"), 8)

    def test_relative_and_clock(self):
        self.assertEqual(parse_when("两小时后叫我", NOW), (datetime(2026, 10, 25, 18, 0), True))
        self.assertEqual(parse_when("半小时后", NOW)[0], datetime(2026, 10, 25, 16, 30))
        self.assertEqual(parse_when("明天下午三点半", NOW), (datetime(2026, 10, 26, 15, 30), True))
        self.assertEqual(parse_when("晚上八点提醒我", NOW), (datetime(2026, 10, 25, 20, 0), True))
        self.assertEqual(parse_when("10点", NOW)[0], datetime(2026, 10, 26, 10, 0))  # 今天 10 点过了 → 明天

    def test_weekdays_and_dates(self):
        self.assertEqual(parse_when("下周三我还要补考", NOW), (datetime(2026, 10, 28, 9, 0), False))
        self.assertEqual(parse_when("周五交论文", NOW)[0], datetime(2026, 10, 30, 9, 0))
        self.assertEqual(parse_when("11月3号考试", NOW)[0], datetime(2026, 11, 3, 9, 0))
        self.assertEqual(parse_when("今天天气不错", NOW)[0], datetime(2026, 10, 25, 9, 0))

    def test_no_time(self):
        self.assertEqual(parse_when("记得提醒我买猫粮", NOW), (None, False))

    def test_event_name_and_ask(self):
        self.assertEqual(event_name("下周三我还要补考，记得提醒我"), "补考")
        self.assertTrue(ASK_RE.search("下周三我还要补考，记得提醒我"))
        self.assertFalse(ASK_RE.search("今天好累"))


class BookTest(unittest.TestCase):
    def test_date_only_event_gets_eve_day_and_followup(self):
        book = ReminderBook(Path(tempfile.mkdtemp()) / "r.json", log=lambda *_: None)
        added = book.add_from_text("下周三我还要补考，记得提醒我", now=NOW)
        times = [datetime.fromtimestamp(i["at"]) for i in added]
        self.assertEqual(times, [datetime(2026, 10, 27, 21, 0), datetime(2026, 10, 28, 8, 30),
                                 datetime(2026, 10, 29, 10, 0)])
        self.assertIn("补考", added[-1]["text"])

    def test_clock_event_and_due_once(self):
        book = ReminderBook(Path(tempfile.mkdtemp()) / "r.json", log=lambda *_: None)
        added = book.add_from_text("两小时后叫我喝水", now=NOW)
        self.assertEqual(len(added), 1)
        later = added[0]["at"] + 1
        self.assertEqual(len(book.due(later)), 1)
        self.assertEqual(book.due(later), [])  # 只提醒一次
        again = ReminderBook(book.path, log=lambda *_: None)
        self.assertTrue(again.items[0]["done"])

    def test_unparsed_returns_none(self):
        book = ReminderBook(Path(tempfile.mkdtemp()) / "r.json", log=lambda *_: None)
        self.assertIsNone(book.add_from_text("记得提醒我买猫粮", now=NOW))


if __name__ == "__main__":
    unittest.main()
