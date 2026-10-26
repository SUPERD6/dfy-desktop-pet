# -*- coding: utf-8 -*-
"""念出来之前的文本清洗：动作、颜文字、符号不念。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voice import clean_for_speech, trim_for_speech  # noqa: E402


class CleanForSpeechTest(unittest.TestCase):
    def test_actions_in_brackets_are_dropped(self):
        self.assertEqual(clean_for_speech("（尾巴摇摇）主人回来啦！"), "主人回来啦！")
        self.assertEqual(clean_for_speech("哼(扭头)才不理你。"), "哼，才不理你。")
        self.assertEqual(clean_for_speech("*抱紧饭碗* 这是本鲸的！"), "这是本鲸的！")
        self.assertEqual(clean_for_speech("【小声】好吧……给你一块饼干"), "好吧，给你一块饼干")

    def test_tilde_ellipsis_and_repeated_punct(self):
        self.assertEqual(clean_for_speech("嘿嘿~~好呀～"), "嘿嘿，好呀")
        self.assertEqual(clean_for_speech("唔……让我想想"), "唔，让我想想")
        self.assertEqual(clean_for_speech("真的吗？！！"), "真的吗？")

    def test_emoji_kaomoji_markdown(self):
        self.assertEqual(clean_for_speech("吃饱啦🍚✨"), "吃饱啦")
        self.assertEqual(clean_for_speech("呜QAQ 又被限流了"), "呜 又被限流了")
        self.assertEqual(clean_for_speech("## 结果\n- 第一项\n- 第二项"), "结果，第一项，第二项")
        self.assertEqual(clean_for_speech("看这里 https://example.com/a?b=1 就行"), "看这里 就行")
        self.assertEqual(clean_for_speech("运行 `python app.py` 就好"), "运行，就好")

    def test_pure_action_or_symbols_say_nothing(self):
        self.assertEqual(clean_for_speech("（蹭蹭）"), "")
        self.assertEqual(clean_for_speech("~~~♪"), "")
        self.assertEqual(clean_for_speech(""), "")

    def test_plain_sentence_unchanged(self):
        self.assertEqual(clean_for_speech("好的，交给我。"), "好的，交给我。")

    def test_long_reply_is_trimmed_at_sentence_end(self):
        long = "第一句话说完了。" * 5 + "最后一句"
        out = trim_for_speech(long, limit=30)
        self.assertTrue(out.endswith("。"))
        self.assertLessEqual(len(out), 30)
        self.assertEqual(trim_for_speech("短句。", limit=30), "短句。")


if __name__ == "__main__":
    unittest.main()
