# -*- coding: utf-8 -*-
"""语义记忆用的小向量模型（nn-3）：bge-small-zh-v1.5（ONNX 量化版，CPU，约 25MB）。

把一句中文变成 512 维单位向量；两句话意思越近，点积越大。记忆召回时和关键词打分一起用，
让「我最近在忙啥」也能想起「主人在准备 PhD 申请」。

模型由 tools/fetch_local_models.py embed 下载到 pet/cache/models/bge-small-zh/；没有模型时 available=False，
记忆退回纯关键词召回。向量按文字的哈希缓存到磁盘（个人数据，跟 memory.json 放一起，不进 Git）。
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "cache" / "models" / "bge-small-zh"
QUERY_PREFIX = ""  # bge 的检索前缀在短口语查询上实测更差（2026-10-28 对照：加前缀后无关句分数反而接近），不用


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class Embedder:
    def __init__(self, model_dir: Path | None = None, cache_path: Path | None = None, log=print):
        self.dir = Path(model_dir or MODEL_DIR)
        self.cache_path = Path(cache_path) if cache_path else None
        self.log = log if callable(log) else print
        self._session = None
        self._tokenizer = None
        self._cache: dict[str, list[float]] = {}
        self._dirty = False
        self._lock = threading.Lock()
        self.reason = ""
        try:
            import numpy  # noqa: F401
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
        except Exception as exc:
            self.reason = f"缺少 onnxruntime/tokenizers：{exc}"
        if not self.reason and not ((self.dir / "model.onnx").is_file() and (self.dir / "tokenizer.json").is_file()):
            self.reason = "缺向量模型（python tools/fetch_local_models.py embed）"
        if self.cache_path and self.cache_path.is_file():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    @property
    def available(self) -> bool:
        return not self.reason

    def _load(self):
        if self._session is None:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            self._session = ort.InferenceSession(str(self.dir / "model.onnx"), options,
                                                 providers=["CPUExecutionProvider"])
            self._tokenizer = Tokenizer.from_file(str(self.dir / "tokenizer.json"))
            self._tokenizer.enable_truncation(max_length=128)
            self._tokenizer.enable_padding()
            self._inputs = {i.name for i in self._session.get_inputs()}
        return self._session

    def _encode(self, texts: list[str]):
        import numpy as np

        session = self._load()
        batch = self._tokenizer.encode_batch(texts)
        feeds = {"input_ids": np.array([b.ids for b in batch], dtype=np.int64),
                 "attention_mask": np.array([b.attention_mask for b in batch], dtype=np.int64)}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(feeds["input_ids"])
        hidden = session.run(None, feeds)[0]
        cls = hidden[:, 0, :]  # bge 用 [CLS] 向量
        return cls / np.maximum(np.linalg.norm(cls, axis=1, keepdims=True), 1e-9)

    def embed(self, texts, query: bool = False):
        """返回 numpy 数组 (n, dim)。记忆条目会缓存；查询（query=True）加前缀、不缓存。"""
        import numpy as np

        texts = [str(t or "").strip() for t in texts]
        if query:
            with self._lock:
                return self._encode([QUERY_PREFIX + t for t in texts])
        with self._lock:
            missing = [t for t in dict.fromkeys(texts) if _key(t) not in self._cache]
            for start in range(0, len(missing), 32):
                chunk = missing[start:start + 32]
                for text, vec in zip(chunk, self._encode(chunk)):
                    self._cache[_key(text)] = [round(float(x), 5) for x in vec]
                self._dirty = True
            return np.array([self._cache[_key(t)] for t in texts], dtype=np.float32)

    def similarities(self, query: str, texts: list[str]):
        """query 和每条 text 的余弦相似度（-1~1）。"""
        if not texts:
            return []
        q = self.embed([query], query=True)[0]
        return [float(x) for x in self.embed(texts) @ q]

    def save(self, keep: set[str] | None = None) -> None:
        """写回向量缓存；keep 给出时只保留这些文字的向量（删掉已忘记的事）。"""
        if not self.cache_path or not self._dirty and keep is None:
            return
        with self._lock:
            data = self._cache
            if keep is not None:
                wanted = {_key(t) for t in keep}
                data = {k: v for k, v in data.items() if k in wanted}
                self._cache = data
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.cache_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(self.cache_path)
                self._dirty = False
            except OSError as exc:
                self.log(f"向量缓存写入失败：{exc}")
