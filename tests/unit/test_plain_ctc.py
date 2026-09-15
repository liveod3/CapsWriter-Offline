"""以合成 CTC token 验证解码和时间戳，不加载模型或 GPU 库。"""

import importlib.util
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def load_source(monkeypatch, name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_fun_ctc_keeps_greedy_tokens_and_timestamps(monkeypatch):
    package = ModuleType("_caps_ctc_test")
    package.__path__ = []
    package.logger = logging.getLogger("ctc-test")
    monkeypatch.setitem(sys.modules, package.__name__, package)
    ctc = load_source(
        monkeypatch,
        "_caps_ctc_test.ctc",
        "core/server/engines/fun_asr_gguf/inference/ctc_decoder.py",
    )
    decoder = ctc.CTCDecoder.__new__(ctc.CTCDecoder)
    decoder.id2token = {0: "苦", 1: "的", 2: "<blank>"}
    decoder.blank_id = 2
    decoder.sess = object()
    indices = np.array([[[0], [0], [2], [1], [1]]])
    decoder._infer = lambda _: (np.zeros_like(indices), indices)
    tokens, stats = decoder.decode(np.zeros((1, 5, 512)), True)
    assert [(t.text, t.timestamp) for t in tokens] == [("苦", 0), ("的", 0.18)]
    assert set(stats) == {"infer", "decode"}
    assert decoder.decode(None, False)[0] == []


def test_sensevoice_uses_only_top1_skips_prompt_and_padded_frames(monkeypatch):
    module = load_source(
        monkeypatch,
        "_sense_decoder_test",
        "core/server/engines/sensevoice_onnx/inference/decoder.py",
    )
    decoder = module.SenseVoiceDecoder.__new__(module.SenseVoiceDecoder)
    indices = np.array([[[99], [99], [99], [99], [1], [1], [0], [2], [2], [99]]])
    decoder.forward = lambda _: (np.zeros_like(indices), indices)
    tokenizer = SimpleNamespace(id_to_piece=lambda i: {1: "苦", 2: "的"}[i])
    assert decoder.decode(None, tokenizer, T_valid=5) == [
        {"text": "苦", "start": 0},
        {"text": "的", "start": 0.18},
    ]
