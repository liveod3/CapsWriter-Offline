
from core.i18n import Notice
import onnxruntime
import numpy as np
import base64
import os
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional
from . import logger

@dataclass
class Token:
    text: str
    timestamp: float

class CTCTokenizer:
    """
    Nano CTC 分词器接口
    """
    def __init__(self, id2token, encode_fn=None):
        self.id2token = id2token
        # 预构建反向查表字典，加速 encode()
        self.token2id = {v: k for k, v in id2token.items()}
        self._piece_size = len(id2token) if id2token else 0
        
    def get_piece_size(self):
        return self._piece_size
        
    def id_to_piece(self, i):
        # 兼容 SentencePiece 接口
        return self.id2token.get(i, f"<{i}>")
        
    def encode(self, text):
        """
        将文本编码为 CTC token ID 列表。
        按字符遍历，在 CTC 词表中查找对应 ID（精确匹配）。
        """
        result = []
        for char in text:
            tid = self.token2id.get(char)
            if tid is not None:
                result.append(tid)
        return result

    def encode_as_pieces(self, text):
        ids = self.encode(text)
        return [self.id_to_piece(i) for i in ids]

class CTCDecoder:
    """FunASR CTC 推理与解码器 (多阶段内部流水线)"""
    def __init__(self, model_path: str, tokens_path: str, onnx_provider: str = 'CPU', dml_pad_to: int = 30):
        self.model_path = model_path
        self.tokens_path = tokens_path
        self.onnx_provider = onnx_provider.upper()
        self.dml_pad_to = dml_pad_to
        
        self.sess = None
        self.id2token = {}
        self.input_dtype = np.float32
        self.tokenizer = None   # CTCTokenizer 包装器
        self._load_tokens()
        
        self._initialize_session()
        self.warmup()


    def _initialize_session(self):
        session_opts = onnxruntime.SessionOptions()
        session_opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        session_opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        session_opts.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        # session_opts.enable_profiling = True
        
        available_providers = onnxruntime.get_available_providers()
        providers = ['CPUExecutionProvider']
        
        if self.onnx_provider in ('TENSORRT', 'TRT') and 'TensorrtExecutionProvider' in available_providers:
            providers.insert(0, ('TensorrtExecutionProvider', {
                'trt_fp16_enable': True,
                'trt_engine_cache_enable': True,
                'trt_engine_cache_path': Path(self.model_path).parent / 'trt_cache',
            }))
        elif self.onnx_provider == 'DML' and 'DmlExecutionProvider' in available_providers:
            providers.insert(0, 'DmlExecutionProvider') 
        elif self.onnx_provider == 'CUDA' and 'CUDAExecutionProvider' in available_providers:
            providers.insert(0, 'CUDAExecutionProvider')
            
        logger.info(Notice('diagnostic.ctc_decoder.ctc_loading_model_providers', value0=os.path.basename(self.model_path), value1=providers))
        
        self.sess = onnxruntime.InferenceSession(
            self.model_path, 
            sess_options=session_opts, 
            providers=providers
        )
        
        # 检测模型输入精度
        in_type = self.sess.get_inputs()[0].type
        self.input_dtype = np.float16 if 'float16' in in_type else np.float32

    def _load_tokens(self):
        self.id2token = load_ctc_tokens(self.tokens_path)
        self.tokenizer = CTCTokenizer(self.id2token)
        
        # 精准寻找 Blank ID：优先匹配包含关键标识的符号
        self.blank_id = None
        for tid, token_text in self.id2token.items():
            clean_text = token_text.lower().strip()
            if clean_text in ("<blk>", "<blank>", "<pad>"):
                self.blank_id = tid
                break
        if self.blank_id is None:
            self.blank_id = max(self.id2token.keys()) if self.id2token else 0
            

    def warmup(self):
        if self.dml_pad_to <= 0:
            return
        target_t_lfr = int((self.dml_pad_to * 100 + 5) // 6) + 1
        dummy_enc = np.zeros((1, target_t_lfr, 512), dtype=self.input_dtype)
        in_name = self.sess.get_inputs()[0].name
        logger.info(Notice('diagnostic.ctc_decoder.ctc_warming_up_fixed_shape_s', value0=self.dml_pad_to))
        self.sess.run(None, {in_name: dummy_enc})

    # ================================================================
    # 对外唯一入口：decode()
    # 返回 CTC tokens 与耗时
    # ================================================================

    def decode(self, enc_output: np.ndarray, enable_ctc: bool) -> tuple:
        """CTC 仅用于原始文本与时间戳，不执行词表替换。"""
        stats = {"infer": 0.0, "decode": 0.0}
        if not enable_ctc or self.sess is None:
            return [], stats
        started = time.perf_counter()
        _, indices = self._infer(enc_output)
        stats["infer"] = time.perf_counter() - started
        started = time.perf_counter()
        _, results = self._greedy_decode(indices[0, :, 0])
        stats["decode"] = time.perf_counter() - started
        return results, stats

    # ================================================================
    # 内部阶段方法
    # ================================================================

    def _infer(self, enc_output: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """阶段 1: ONNX 推理，返回 (topk_log_probs, topk_indices)"""
        outputs = self.sess.run(None, {"enc_output": enc_output})
        return outputs[0], outputs[1]

    def _greedy_decode(self, top1_indices: np.ndarray) -> Tuple[str, List[Token]]:
        """阶段 2: 基于 Top-1 Index 的贪婪解码"""
        ctc_text, ctc_results, _ = decode_ctc_indices(top1_indices, self.id2token)
        return ctc_text, ctc_results






def load_ctc_tokens(filename):
    """加载 CTC 词表"""
    id2token = dict()
    if not os.path.exists(filename):
        return id2token
    with open(filename, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if not parts: continue
            if len(parts) == 1:
                t, i = " ", parts[0]
            else:
                t, i = parts
            
            # Pre-decode base64 here to save time during inference
            try:
                # Some tokens might rely on being decoded, do it once
                token_text = base64.b64decode(t).decode("utf-8")
            except:
                token_text = t
                
            id2token[int(i)] = token_text
                
    return id2token

def decode_ctc_indices(indices, id2token):
    """
    Greedy search 贪心解码 (直接基于 Indices)。
    """
    t0 = time.perf_counter()
    blank_id = max(id2token.keys()) if id2token else 0
    
    frame_shift_ms = 60
    
    # 1. Collapse repeats
    collapsed = []
    if len(indices) > 0:
        current_id = indices[0]
        start_idx = 0
        for i in range(1, len(indices)):
            if indices[i] != current_id:
                collapsed.append((current_id, start_idx))
                current_id = indices[i]
                start_idx = i
        collapsed.append((current_id, start_idx))

    results = []

    # 2. Filter blanks and decode text
    for token_id, start in collapsed:
        if token_id == blank_id:
            continue

        token_text = id2token.get(token_id, "")
        if not token_text: continue

        # Calculate time (只计算起始位置)
        t_timestamp = max((start * frame_shift_ms) / 1000.0, 0.0)

        results.append(Token(
            text=token_text,
            timestamp=t_timestamp
        ))
                
    full_text = "".join([r.text for r in results])
    t_loop = time.perf_counter() - t0
    
    timings = {
        "cast": 0.0,
        "argmax": 0.0,
        "loop": t_loop
    }
    return full_text, results, timings

