
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
    Nano CTC tokenizer interface.
    """
    def __init__(self, id2token, encode_fn=None):
        self.id2token = id2token
        # Precompute reverse lookup for encode().
        self.token2id = {v: k for k, v in id2token.items()}
        self._piece_size = len(id2token) if id2token else 0
        
    def get_piece_size(self):
        return self._piece_size
        
    def id_to_piece(self, i):
        # SentencePiece-compatible interface.
        return self.id2token.get(i, f"<{i}>")
        
    def encode(self, text):
        """
        Encode text as CTC token IDs.
        Look up each character by exact match in the CTC vocabulary.
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
    """Run FunASR CTC inference and decoding in separate stages."""
    def __init__(self, model_path: str, tokens_path: str, onnx_provider: str = 'CPU', dml_pad_to: int = 30):
        self.model_path = model_path
        self.tokens_path = tokens_path
        self.onnx_provider = onnx_provider.upper()
        self.dml_pad_to = dml_pad_to
        
        self.sess = None
        self.id2token = {}
        self.input_dtype = np.float32
        self.tokenizer = None   # CTCTokenizer adapter.
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
        
        # Detect model input precision.
        in_type = self.sess.get_inputs()[0].type
        self.input_dtype = np.float16 if 'float16' in in_type else np.float32

    def _load_tokens(self):
        self.id2token = load_ctc_tokens(self.tokens_path)
        self.tokenizer = CTCTokenizer(self.id2token)
        
        # Prefer explicit blank-token markers when resolving the blank ID.
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
    # Public entry point: decode().
    # Return CTC tokens and elapsed time.
    # ================================================================

    def decode(self, enc_output: np.ndarray, enable_ctc: bool) -> tuple:
        """Use CTC for raw text and timestamps without vocabulary replacement."""
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
    # Internal stages.
    # ================================================================

    def _infer(self, enc_output: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Run ONNX inference and return top-k log probabilities and indexes."""
        outputs = self.sess.run(None, {"enc_output": enc_output})
        return outputs[0], outputs[1]

    def _greedy_decode(self, top1_indices: np.ndarray) -> Tuple[str, List[Token]]:
        """Decode greedily from top-1 indexes."""
        ctc_text, ctc_results, _ = decode_ctc_indices(top1_indices, self.id2token)
        return ctc_text, ctc_results






def load_ctc_tokens(filename):
    """Load the CTC vocabulary."""
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
    Decode greedily from token indexes.
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

        # Calculate token start times only.
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

