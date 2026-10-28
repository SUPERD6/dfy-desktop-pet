# -*- coding: utf-8 -*-
"""本机神经网络模块（2026-10-28）：耳朵（唤醒词/听写/语气）、语义记忆、本机嗓子客户端。

模型不在仓库里（tools/fetch_local_models.py 下载到 pet/cache/models/）；没有模型的机器上相关测试自动跳过。
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PET))

import ears  # noqa: E402
from embedder import Embedder  # noqa: E402
from memory import MemoryStore  # noqa: E402

HAS_EARS = ears.Ears(log=lambda *a: None).available
HAS_EMBED = Embedder(log=lambda *a: None).available


class KeywordTest(unittest.TestCase):
    def test_keyword_line_is_tone_pinyin(self):
        self.assertEqual(ears.keyword_line("大肥鱼"), "d à f éi y ú @大肥鱼")
        self.assertEqual(ears.keyword_line("小肥鱼"), "x iǎo f éi y ú @小肥鱼")

    def test_short_phrases_dropped_by_default(self):
        logs = []
        listener = ears.EarsWakeListener(ears.Ears(log=lambda *a: None), ["大肥鱼", "肥鱼"], log=logs.append)
        self.assertEqual(listener.phrases, ["大肥鱼"])
        self.assertTrue(any("太短" in line for line in logs))
        keep = ears.EarsWakeListener(ears.Ears(log=lambda *a: None), ["大肥鱼", "肥鱼"], log=lambda *a: None,
                                     allow_short=True)
        self.assertEqual(keep.phrases, ["大肥鱼", "肥鱼"])

    def test_heard_tone(self):
        self.assertEqual(ears.Heard(text="x", emotion="难过", event="笑").tone, "语气难过，笑")
        self.assertEqual(ears.Heard(text="x").tone, "")


@unittest.skipUnless(HAS_EARS, "没下载本机耳朵模型")
class EarsModelTest(unittest.TestCase):
    def test_kws_and_sensevoice_on_bundled_samples(self):
        import wave

        import numpy as np

        e = ears.Ears(log=lambda *a: None)
        sample = e.dir / "sensevoice" / "test_wavs" / "zh.wav"
        with wave.open(str(sample)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        heard = e.transcribe(audio)
        self.assertIn("九点", heard.text)  # 样例：「开放时间早上九点至下午五点」
        folder = Path(tempfile.mkdtemp())
        try:
            kws = e.keyword_spotter(["大肥鱼"], folder / "kw.txt")
            self.assertIn("@大肥鱼", (folder / "kw.txt").read_text(encoding="utf-8"))
            stream = kws.create_stream()
            stream.accept_waveform(16000, audio)  # 这段话里没有「大肥鱼」：不该唤醒
            hits = []
            while kws.is_ready(stream):
                kws.decode_stream(stream)
                if kws.get_result(stream):
                    hits.append(1)
            self.assertEqual(hits, [])
        finally:
            shutil.rmtree(folder, ignore_errors=True)


@unittest.skipUnless(HAS_EMBED, "没下载向量模型")
class SemanticMemoryTest(unittest.TestCase):
    FACTS = ["主人在准备美国 PhD 申请", "主人喜欢喝冰美式", "主人养了一只橘猫叫团子", "主人下周三有期中考试",
             "主人最近在学强化学习", "主人不喜欢吃香菜", "主人住在宾夕法尼亚", "主人每天晚上十二点后才睡",
             "主人的生日是三月五号", "主人在读电气工程硕士"]

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = MemoryStore(self.dir / "memory.json", log=lambda *a: None)
        for text in self.FACTS:
            self.store.add_fact(text)
        self.store.embedder = Embedder(cache_path=self.dir / "memory.vectors.json", log=lambda *a: None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def recalled(self, query):
        return [item["text"] for item in self.store.relevant(query)]

    def test_paraphrase_recalled(self):
        """换个说法也能想起来：关键词完全不沾边。"""
        self.assertIn("主人喜欢喝冰美式", self.recalled("我想喝点咖啡"))
        self.assertIn("主人养了一只橘猫叫团子", self.recalled("我家猫今天又捣乱了"))
        self.assertIn("主人每天晚上十二点后才睡", self.recalled("我又熬夜了"))

    def test_unrelated_small_talk_recalls_nothing_semantic(self):
        self.assertEqual(self.recalled("今天天气不错"), [])

    def test_without_embedder_falls_back_to_keywords(self):
        self.store.embedder = None
        self.assertEqual(self.recalled("我想喝点咖啡"), [])
        self.assertIn("主人下周三有期中考试", self.recalled("期中考试好难"))

    def test_vector_cache_saved_next_to_memory(self):
        self.recalled("我想喝点咖啡")
        self.store.save()
        cache = json.loads((self.dir / "memory.vectors.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cache), len(self.FACTS))


class QwenVoiceClientTest(unittest.TestCase):
    """voice.py 的 qwen3 后端：按语气请求、缓存、服务不在时退回。用假服务器，不需要 GPU。"""

    @classmethod
    def setUpClass(cls):
        calls = cls.calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                calls.append(body)
                data = b"RIFF" + b"\0" * 2000
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def engine(self, port):
        from voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)  # 不起播放线程，只测合成
        engine.cache = Path(tempfile.mkdtemp())
        engine.config = {"voice_backend": "qwen3", "qwen_tts": {"base_url": f"http://127.0.0.1:{port}"}}
        engine.log = lambda *a: None
        return engine

    def test_tone_sent_and_cached(self):
        engine = self.engine(self.server.server_address[1])
        first = engine._synthesize_qwen("主人回来啦", "happy")
        again = engine._synthesize_qwen("主人回来啦", "happy")
        self.assertEqual(first, again)
        self.assertEqual(self.calls[-1], {"text": "主人回来啦", "tone": "happy"})
        self.assertEqual(sum(1 for c in self.calls if c["text"] == "主人回来啦"), 1)  # 第二次走缓存

    def test_voice_id_changes_with_mix(self):
        """换音色或混合比例后缓存要失效，否则会继续放旧声音。"""
        from voice import qwen_voice_id

        plain = qwen_voice_id({"voice": "mingyue-v2", "mix_ref": "docs/seedvc-sample.wav", "mix_weight": 0})
        mixed = qwen_voice_id({"voice": "mingyue-v2", "mix_ref": "docs/seedvc-sample.wav", "mix_weight": 0.3})
        self.assertNotEqual(plain, mixed)
        self.assertEqual(mixed, "mingyue-v2+seedvc-sample30-lock2")
        self.assertNotEqual(qwen_voice_id({}), plain)

    def test_service_down_returns_none(self):
        engine = self.engine(1)  # 没人监听的端口
        self.assertIsNone(engine._synthesize_qwen("在吗", "neutral"))


if __name__ == "__main__":
    unittest.main()
