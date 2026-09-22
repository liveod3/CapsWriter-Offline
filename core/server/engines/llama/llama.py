
from core.i18n import Notice
import sys
import os
import ctypes
import codecs
import struct
import time
import logging
from collections import deque, Counter
import numpy as np
import gguf
from gguf.constants import GGML_QUANT_SIZES, GGMLQuantizationType
from typing import List, Union, Set, Optional
from pathlib import Path
from os.path import relpath
from . import logger

# =========================================================================
# Configuration
# =========================================================================
LOGS = True      # Forward llama.cpp diagnostics to the log file.

# =========================================================================
# Type Definitions
# =========================================================================

llama_token = ctypes.c_int32
llama_pos = ctypes.c_int32
llama_seq_id = ctypes.c_int32

class llama_model_params(ctypes.Structure):
    _fields_ = [
        ("devices", ctypes.POINTER(ctypes.c_void_p)),
        ("tensor_buft_overrides", ctypes.POINTER(ctypes.c_void_p)),
        ("n_gpu_layers", ctypes.c_int32),
        ("split_mode", ctypes.c_int32),
        ("main_gpu", ctypes.c_int32),
        ("tensor_split", ctypes.POINTER(ctypes.c_float)),
        ("progress_callback", ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_float, ctypes.c_void_p)),
        ("progress_callback_user_data", ctypes.c_void_p),
        ("kv_overrides", ctypes.POINTER(ctypes.c_void_p)),
        ("vocab_only", ctypes.c_bool),
        ("use_mmap", ctypes.c_bool),
        ("use_direct_io", ctypes.c_bool),
        ("use_mlock", ctypes.c_bool),
        ("check_tensors", ctypes.c_bool),
        ("use_extra_bufts", ctypes.c_bool),
        ("no_host", ctypes.c_bool),
        ("no_alloc", ctypes.c_bool),
    ]

class llama_context_params(ctypes.Structure):
    _fields_ = [
        ("n_ctx", ctypes.c_uint32),
        ("n_batch", ctypes.c_uint32),
        ("n_ubatch", ctypes.c_uint32),
        ("n_seq_max", ctypes.c_uint32),
        ("n_threads", ctypes.c_int32),
        ("n_threads_batch", ctypes.c_int32),
        ("rope_scaling_type", ctypes.c_int32),
        ("pooling_type", ctypes.c_int32),
        ("attention_type", ctypes.c_int32),
        ("flash_attn_type", ctypes.c_int32),
        ("rope_freq_base", ctypes.c_float),
        ("rope_freq_scale", ctypes.c_float),
        ("yarn_ext_factor", ctypes.c_float),
        ("yarn_attn_factor", ctypes.c_float),
        ("yarn_beta_fast", ctypes.c_float),
        ("yarn_beta_slow", ctypes.c_float),
        ("yarn_orig_ctx", ctypes.c_uint32),
        ("defrag_thold", ctypes.c_float),
        ("cb_eval", ctypes.c_void_p),
        ("cb_eval_user_data", ctypes.c_void_p),
        ("type_k", ctypes.c_int32),
        ("type_v", ctypes.c_int32),
        ("abort_callback", ctypes.c_void_p),
        ("abort_callback_data", ctypes.c_void_p),
        ("embeddings", ctypes.c_bool),
        ("offload_kqv", ctypes.c_bool),
        ("no_perf", ctypes.c_bool),
        ("op_offload", ctypes.c_bool),
        ("swa_full", ctypes.c_bool),
        ("kv_unified", ctypes.c_bool),
        ("samplers", ctypes.POINTER(ctypes.c_void_p)),
        ("n_samplers", ctypes.c_size_t),
    ]

class llama_sampler_chain_params(ctypes.Structure):
    _fields_ = [
        ("no_perf", ctypes.c_bool),
    ]

class llama_logit_bias(ctypes.Structure):
    _fields_ = [
        ("token", llama_token),
        ("bias", ctypes.c_float),
    ]

class llama_batch(ctypes.Structure):
    _fields_ = [
        ("n_tokens", ctypes.c_int32),
        ("token", ctypes.POINTER(llama_token)),
        ("embd", ctypes.POINTER(ctypes.c_float)),
        ("pos", ctypes.POINTER(llama_pos)),
        ("n_seq_id", ctypes.POINTER(ctypes.c_int32)),
        ("seq_id", ctypes.POINTER(ctypes.POINTER(llama_seq_id))),
        ("logits", ctypes.POINTER(ctypes.c_int8)),
    ]

# =========================================================================
# Llama.cpp Library Bindings
# =========================================================================

# Global library references
llama = None
ggml = None
ggml_base = None

# Global function pointers
llama_log_set = None
llama_backend_init = None
llama_backend_free = None
llama_model_default_params = None
llama_model_load_from_file = None
llama_model_free = None
llama_model_get_vocab = None
llama_context_default_params = None
llama_init_from_model = None
llama_free = None
llama_batch_init = None
llama_batch_free = None
llama_decode = None
llama_get_logits = None
llama_get_logits_ith = None
llama_get_embeddings = None
llama_tokenize = None
llama_vocab_n_tokens = None
llama_vocab_eos = None
llama_token_to_piece = None
llama_get_memory = None
llama_memory_clear = None
llama_model_n_embd = None

# Sampler
llama_sampler_chain_default_params = None
llama_sampler_chain_init = None
llama_sampler_chain_add = None
llama_sampler_init_greedy = None
llama_sampler_init_dist = None
llama_sampler_init_temp = None
llama_sampler_init_top_k = None
llama_sampler_init_top_p = None
llama_sampler_sample = None
llama_sampler_free = None
llama_sampler_init_min_p = None
llama_sampler_init_penalties = None
llama_sampler_accept = None

def logger_callback(level, message, user_data):
    """Forward native diagnostics using the ggml_log_level severity."""
    if not message:
        return
    try:
        text = message.decode('utf-8', errors='replace').strip()
        if not text or text == '.':
            return
        # ggml/include/ggml.h: DEBUG=1, INFO=2, WARN=3, ERROR=4.
        # NONE/CONT and unknown values have no standalone severity.
        severity = {1: logging.DEBUG, 2: logging.INFO,
                    3: logging.WARNING, 4: logging.ERROR}.get(level, logging.DEBUG)
        logger.log(severity, '%s', text, extra={'markup': False})
    except Exception as exc:
        logger.warning(Notice('diagnostic.llama.native_log_callback_failed_error'), type(exc).__name__)

def configure_logging(logs=True):
    """Configure the llama.cpp log callback."""
    global _log_callback_ref
    LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)
    if logs:
        _log_callback_ref = LOG_CALLBACK(logger_callback)
    else:
        _log_callback_ref = LOG_CALLBACK(lambda l, m, u: None)
    llama_log_set(_log_callback_ref, None)

def bind_llama_lib():
    """Bind the llama.cpp API."""
    global llama, ggml, ggml_base
    global llama_log_set, llama_backend_init, llama_backend_free
    global llama_model_default_params, llama_model_load_from_file, llama_model_free, llama_model_get_vocab
    global llama_context_default_params, llama_init_from_model, llama_free
    global llama_batch_init, llama_batch_free, llama_batch_get_one
    global llama_decode, llama_get_logits, llama_get_logits_ith, llama_get_embeddings, llama_tokenize
    global llama_get_memory, llama_memory_clear, llama_model_n_embd
    global llama_vocab_n_tokens, llama_vocab_eos, llama_token_to_piece
    global llama_sampler_chain_default_params, llama_sampler_chain_init, llama_sampler_chain_add
    global llama_sampler_init_greedy, llama_sampler_init_dist, llama_sampler_init_temp
    global llama_sampler_init_top_k, llama_sampler_init_top_p, llama_sampler_sample, llama_sampler_free
    global llama_sampler_init_min_p, llama_sampler_init_penalties, llama_sampler_accept
    global llama_sampler_init_logit_bias
    global _log_callback_ref

    if llama is not None:
        return

    # Locate the library in the module's bin directory.
    lib_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")

    # Resolve platform library naming.
    if sys.platform == "win32":
        GGML_DLL = "ggml.dll"
        GGML_BASE_DLL = "ggml-base.dll"
        LLAMA_DLL = "llama.dll"
    elif sys.platform == "darwin":
        GGML_DLL = "libggml.dylib"
        GGML_BASE_DLL = "libggml-base.dylib"
        LLAMA_DLL = "libllama.dylib"
    else:
        GGML_DLL = "libggml.so"
        GGML_BASE_DLL = "libggml-base.so"
        LLAMA_DLL = "libllama.so"

    ggml = ctypes.CDLL(os.path.join(lib_dir, GGML_DLL))
    ggml_base = ctypes.CDLL(os.path.join(lib_dir, GGML_BASE_DLL))
    llama = ctypes.CDLL(os.path.join(lib_dir, LLAMA_DLL))

    # Set the log callback.
    LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)
    llama_log_set = llama.llama_log_set
    llama_log_set.argtypes = [LOG_CALLBACK, ctypes.c_void_p]
    llama_log_set.restype = None
    configure_logging(logs=LOGS)

    # Load backends.
    ggml_backend_load_all = ggml.ggml_backend_load_all
    ggml_backend_load_all.argtypes = []
    ggml_backend_load_all.restype = None
    ggml_backend_load_all()

    llama_backend_init = llama.llama_backend_init
    llama_backend_init.argtypes = []
    llama_backend_init.restype = None
    llama_backend_init()

    # Bind remaining functions.
    llama_backend_free = llama.llama_backend_free
    llama_backend_free.argtypes = []
    llama_backend_free.restype = None

    llama_model_default_params = llama.llama_model_default_params
    llama_model_default_params.argtypes = []
    llama_model_default_params.restype = llama_model_params

    llama_model_load_from_file = llama.llama_model_load_from_file
    llama_model_load_from_file.argtypes = [ctypes.c_char_p, llama_model_params]
    llama_model_load_from_file.restype = ctypes.c_void_p

    llama_model_free = llama.llama_model_free
    llama_model_free.argtypes = [ctypes.c_void_p]
    llama_model_free.restype = None

    llama_model_get_vocab = llama.llama_model_get_vocab
    llama_model_get_vocab.argtypes = [ctypes.c_void_p]
    llama_model_get_vocab.restype = ctypes.c_void_p

    llama_model_n_embd = llama.llama_model_n_embd
    llama_model_n_embd.argtypes = [ctypes.c_void_p]
    llama_model_n_embd.restype = ctypes.c_int32

    # Context
    llama_context_default_params = llama.llama_context_default_params
    llama_context_default_params.argtypes = []
    llama_context_default_params.restype = llama_context_params

    llama_init_from_model = llama.llama_init_from_model
    llama_init_from_model.argtypes = [ctypes.c_void_p, llama_context_params]
    llama_init_from_model.restype = ctypes.c_void_p

    llama_free = llama.llama_free
    llama_free.argtypes = [ctypes.c_void_p]
    llama_free.restype = None

    # Batch
    llama_batch_init = llama.llama_batch_init
    llama_batch_init.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
    llama_batch_init.restype = llama_batch

    llama_batch_free = llama.llama_batch_free
    llama_batch_free.argtypes = [llama_batch]
    llama_batch_free.restype = None
    
    llama_batch_get_one = llama.llama_batch_get_one
    llama_batch_get_one.argtypes = [ctypes.POINTER(llama_token), ctypes.c_int32]
    llama_batch_get_one.restype = llama_batch

    # Decode
    llama_decode = llama.llama_decode
    llama_decode.argtypes = [ctypes.c_void_p, llama_batch]
    llama_decode.restype = ctypes.c_int32

    # Logits
    llama_get_logits = llama.llama_get_logits
    llama_get_logits.argtypes = [ctypes.c_void_p]
    llama_get_logits.restype = ctypes.POINTER(ctypes.c_float)

    llama_get_logits_ith = llama.llama_get_logits_ith
    llama_get_logits_ith.argtypes = [ctypes.c_void_p, ctypes.c_int32]
    llama_get_logits_ith.restype = ctypes.POINTER(ctypes.c_float)

    llama_get_embeddings = llama.llama_get_embeddings
    llama_get_embeddings.argtypes = [ctypes.c_void_p]
    llama_get_embeddings.restype = ctypes.POINTER(ctypes.c_float)

    # Tokenize
    llama_tokenize = llama.llama_tokenize
    llama_tokenize.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
        ctypes.POINTER(llama_token), ctypes.c_int32,
        ctypes.c_bool, ctypes.c_bool,
    ]
    llama_tokenize.restype = ctypes.c_int32

    # Vocab
    llama_vocab_n_tokens = llama.llama_vocab_n_tokens
    llama_vocab_n_tokens.argtypes = [ctypes.c_void_p]
    llama_vocab_n_tokens.restype = ctypes.c_int32

    llama_vocab_eos = llama.llama_vocab_eos
    llama_vocab_eos.argtypes = [ctypes.c_void_p]
    llama_vocab_eos.restype = llama_token

    llama_token_to_piece = llama.llama_token_to_piece
    llama_token_to_piece.argtypes = [ctypes.c_void_p, llama_token, ctypes.c_char_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_bool]
    llama_token_to_piece.restype = ctypes.c_int

    # Memory (KV Cache)
    llama_get_memory = llama.llama_get_memory
    llama_get_memory.argtypes = [ctypes.c_void_p]
    llama_get_memory.restype = ctypes.c_void_p

    llama_memory_clear = llama.llama_memory_clear
    llama_memory_clear.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    llama_memory_clear.restype = None

    # Sampler
    llama_sampler_chain_default_params = llama.llama_sampler_chain_default_params
    llama_sampler_chain_default_params.argtypes = []
    llama_sampler_chain_default_params.restype = llama_sampler_chain_params

    llama_sampler_chain_init = llama.llama_sampler_chain_init
    llama_sampler_chain_init.argtypes = [llama_sampler_chain_params]
    llama_sampler_chain_init.restype = ctypes.c_void_p

    llama_sampler_chain_add = llama.llama_sampler_chain_add
    llama_sampler_chain_add.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    llama_sampler_chain_add.restype = None

    llama_sampler_init_greedy = llama.llama_sampler_init_greedy
    llama_sampler_init_greedy.argtypes = []
    llama_sampler_init_greedy.restype = ctypes.c_void_p

    llama_sampler_init_dist = llama.llama_sampler_init_dist
    llama_sampler_init_dist.argtypes = [ctypes.c_uint32]
    llama_sampler_init_dist.restype = ctypes.c_void_p

    llama_sampler_init_temp = llama.llama_sampler_init_temp
    llama_sampler_init_temp.argtypes = [ctypes.c_float]
    llama_sampler_init_temp.restype = ctypes.c_void_p

    llama_sampler_init_top_k = llama.llama_sampler_init_top_k
    llama_sampler_init_top_k.argtypes = [ctypes.c_int32]
    llama_sampler_init_top_k.restype = ctypes.c_void_p

    llama_sampler_init_top_p = llama.llama_sampler_init_top_p
    llama_sampler_init_top_p.argtypes = [ctypes.c_float, ctypes.c_size_t]
    llama_sampler_init_top_p.restype = ctypes.c_void_p

    llama_sampler_sample = llama.llama_sampler_sample
    llama_sampler_sample.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32]
    llama_sampler_sample.restype = llama_token

    llama_sampler_free = llama.llama_sampler_free
    llama_sampler_free.argtypes = [ctypes.c_void_p]
    llama_sampler_free.restype = None

    llama_sampler_init_logit_bias = llama.llama_sampler_init_logit_bias
    llama_sampler_init_logit_bias.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.POINTER(llama_logit_bias)]
    llama_sampler_init_logit_bias.restype = ctypes.c_void_p

    llama_sampler_init_min_p = llama.llama_sampler_init_min_p
    llama_sampler_init_min_p.argtypes = [ctypes.c_float, ctypes.c_size_t]
    llama_sampler_init_min_p.restype = ctypes.c_void_p

    llama_sampler_init_penalties = llama.llama_sampler_init_penalties
    llama_sampler_init_penalties.argtypes = [ctypes.c_int32, ctypes.c_float, ctypes.c_float, ctypes.c_float]
    llama_sampler_init_penalties.restype = ctypes.c_void_p

    llama_sampler_accept = llama.llama_sampler_accept
    llama_sampler_accept.argtypes = [ctypes.c_void_p, llama_token]
    llama_sampler_accept.restype = None

def init():
    """
    Initialize llama.cpp from its library directory.
    """
    original_cwd = Path.cwd()
    lib_dir = Path(__file__).parent / 'bin'

    # Switch to the DLL directory and add it to PATH.
    os.chdir(lib_dir)
    os.environ['PATH'] = os.getcwd() + os.pathsep + os.environ['PATH']
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(os.getcwd())
    logger.info(Notice('diagnostic.llama.initializing_llama_cpp_switching_directory_to', value0=Path.cwd()))

    # Bind the llama API.
    bind_llama_lib()
    
    # Restore the original directory.
    os.chdir(original_cwd)
    logger.info(Notice('diagnostic.llama.restoring_directory', value0=Path.cwd()))
    
    return True

init()


# =========================================================================
# High-level llama.cpp API.
# =========================================================================


class LlamaModel:
    """Wrap a native model."""
    def __init__(self, path, n_gpu_layers=-1, use_gpu=1):
        self.ptr = self.load_model(path, n_gpu_layers=n_gpu_layers, use_gpu=use_gpu)
            
        self.vocab = llama_model_get_vocab(self.ptr)
        self.n_embd = llama_model_n_embd(self.ptr)
        self.eos_token = llama_vocab_eos(self.vocab)

    def load_model(self, model_path: str, n_gpu_layers: int = -1, use_gpu: bool = 0):
        """
        Load GGUF with backend initialization and path encoding.
        
        Args:
            model_path: GGUF model path.
            n_gpu_layers: Layers to offload to GPU; -1 means all layers.
            use_gpu: Enable GPU use; False forces CPU execution.
            
        Returns:
            model: llama_model pointer.
        """
        
        model_path = Path(model_path)

        model_params = llama_model_default_params()
        model_params.n_gpu_layers = n_gpu_layers
        if not use_gpu:
            model_params.devices = (ctypes.c_void_p * 1)(None)
        
        model = llama_model_load_from_file(
            model_path.as_posix().encode('utf-8'),
            model_params
        )

        if model:
            return model
        else:
            logger.error(Notice('diagnostic.llama.model_loading_failed', value0=model_path))
            return None

    def tokenize(self, text: str, add_special: bool = False, parse_special: bool = True) -> List[int]:
        """Tokenize text through the native API."""
        return text_to_tokens(self.vocab, text, add_special, parse_special)

    def detokenize(self, tokens: List[int]) -> str:
        """Decode token IDs through the native API."""
        if tokens is None or len(tokens) == 0: return ""
        all_bytes = b"".join([self.token_to_bytes(tid) for tid in tokens])
        return all_bytes.decode('utf-8', errors='replace')

    def token_to_bytes(self, token_id: int) -> bytes:
        """Convert a native token to bytes."""
        return token_to_bytes(self.vocab, token_id)
        
    def token_to_piece(self, token_id: int) -> str:
        """Convert a native token to a text piece."""
        return self.token_to_bytes(token_id).decode('utf-8', errors='replace')

    def token_bos(self) -> int:
        return llama_vocab_bos(self.vocab)

    def token_eos(self) -> int:
        return llama_vocab_eos(self.vocab)
        
    def token_to_id(self, text: str) -> int:
        """Resolve an exact native token string to its ID."""
        # Find the ID through tokenization.
        res = self.tokenize(text, add_special=False, parse_special=True)
        return res[0] if res else -1

    def __del__(self):
        if hasattr(self, 'ptr') and self.ptr:
            llama_model_free(self.ptr)
            self.ptr = None

class LlamaContext:
    """Wrap a native context."""
    def __init__(self, model, n_ctx=2048, n_batch=2048, n_ubatch=512, n_seq_max=1, 
                 embeddings=False, pooling_type=0, flash_attn=True, 
                 offload_kqv=True, no_perf=True, n_threads=None, n_threads_batch=None):
        self.model = model # Retain the model to prevent early release.
        params = llama_context_default_params()
        params.n_ctx = n_ctx
        params.n_batch = n_batch
        params.n_ubatch = n_ubatch
        params.n_seq_max = n_seq_max
        params.embeddings = embeddings
        params.pooling_type = pooling_type
        params.flash_attn_type = 1 if flash_attn else 0
        params.offload_kqv = offload_kqv
        params.no_perf = no_perf
        
        # Thread settings.
        cpu_count = os.cpu_count() or 4
        if n_threads:
            params.n_threads = n_threads
        else:
            params.n_threads = cpu_count // 2

        if n_threads_batch:
            params.n_threads_batch = n_threads_batch
        else:
            params.n_threads_batch = n_threads if n_threads else cpu_count

        self.ptr = llama_init_from_model(model.ptr, params)
        if not self.ptr:
            raise RuntimeError(Notice('validation.llama.context_initialization_failed'))

    def decode(self, batch):
        struct = batch.struct if hasattr(batch, 'struct') else batch
        return llama_decode(self.ptr, struct)

    def decode_token(self, token_id):
        """
        Set a single-token batch and decode it in one operation.
        """
        return self.decode(get_one_batch(token_id))

    def get_logits(self):
        """Return output for the last batch token with logits enabled."""
        return llama_get_logits(self.ptr)

    def get_logits_ith(self, i: int):
        """Return logits for batch token i, which must have logits enabled."""
        return llama_get_logits_ith(self.ptr, i)

    def get_embeddings(self):
        return llama_get_embeddings(self.ptr)

    def clear_kv_cache(self):
        mem = llama_get_memory(self.ptr)
        llama_memory_clear(mem, True)

    def __del__(self):
        if hasattr(self, 'ptr') and self.ptr:
            llama_free(self.ptr)
            self.ptr = None

class LlamaBatch:
    """Wrap a native batch with direct attribute access."""
    def __init__(self, n_tokens, embd_dim=0, n_seq_max=1):
        self.struct = llama_batch_init(n_tokens, embd_dim, n_seq_max)
        self.n_tokens_max = n_tokens

    @property
    def n_tokens(self): return self.struct.n_tokens
    @n_tokens.setter
    def n_tokens(self, val): self.struct.n_tokens = val

    @property
    def token(self): return self.struct.token
    @property
    def embd(self): return self.struct.embd
    @property
    def pos(self): return self.struct.pos
    @property
    def n_seq_id(self): return self.struct.n_seq_id
    @property
    def seq_id(self): return self.struct.seq_id
    @property
    def logits(self): return self.struct.logits

    def set_embd(self, data: np.ndarray, pos: Union[np.ndarray, int] = 0, seq_id: int = 0):
        """
        Populate embeddings and initialize positions.
        
        Args:
            data: Embedding array [n_tokens, dim].
            pos: Position data.
                 - int: Starting offset for generated sequential positions.
                 - np.ndarray: Copy into the position buffer for layouts such as Qwen3.
            seq_id: Sequence identifier.
        """
        n_tokens = data.shape[0]
        if n_tokens > self.n_tokens_max:
            raise ValueError(Notice('validation.llama.insufficient_batch_capacity', value0=n_tokens, value1=self.n_tokens_max))
        
        # 1. Copy embeddings.
        if not data.flags['C_CONTIGUOUS']:
            data = np.ascontiguousarray(data)
        ctypes.memmove(self.embd, data.ctypes.data, data.nbytes)
        
        # 2. Populate positions.
        if isinstance(pos, int):
            # Generate sequential positions.
            pos_offset = pos
            for i in range(n_tokens):
                self.pos[i] = pos_offset + i
        elif isinstance(pos, np.ndarray):
            # Use supplied positions, including Qwen3 multiplane layouts.
            # Position length can differ from n_tokens because of stride.
            # It must still fit within the batch capacity.
            if not pos.flags['C_CONTIGUOUS']:
                pos = np.ascontiguousarray(pos)
            
            # Copy directly with memmove.
            # self.pos is a ctypes pointer.
            ctypes.memmove(self.pos, pos.ctypes.data, pos.nbytes)
        else:
            raise TypeError(Notice('validation.llama.unsupported_pos_type', value0=type(pos)))

        # 3. Set remaining metadata.
        self.n_tokens = n_tokens
        for i in range(n_tokens):
            self.n_seq_id[i] = 1
            self.seq_id[i][0] = seq_id
            self.logits[i] = 1 if i == n_tokens - 1 else 0
        
        return self

    def __del__(self):
        if hasattr(self, 'struct'):
            llama_batch_free(self.struct)

def get_one_batch(token_id: int):
    """
    Construct a single-token batch without native batch allocation.
    Equivalent to C++ llama_batch_get_one(&token, 1).
    Avoid llama_batch_init allocation and let the backend infer positions.
    """
    token_arr = (llama_token * 1)(token_id)
    return llama_batch_get_one(token_arr, 1)

class LlamaSampler:
    """Wrap a native sampler."""
    def __init__(
        self, 
        temperature: float = 0.8, 
        top_k: int = 50, 
        top_p: float = 1.0, 
        min_p: float = 0.0,
        repeat_penalty: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        penalty_last_n: int = 64,
        seed: Optional[int] = None, 
        logit_bias: Optional[dict] = None, 
        n_vocab: int = 0
    ):
        import time
        if seed is None:
            seed = int(time.time())
            
        sparams = llama_sampler_chain_default_params()
        self.ptr = llama_sampler_chain_init(sparams)
        
        # 1. Apply logit bias first.
        if logit_bias and n_vocab > 0 and isinstance(logit_bias, dict):
            n_bias = len(logit_bias)
            BiasArray = llama_logit_bias * n_bias
            bias_data = BiasArray()
            for i, (token, bias) in enumerate(logit_bias.items()):
                bias_data[i].token = token
                bias_data[i].bias = bias
            llama_sampler_chain_add(self.ptr, llama_sampler_init_logit_bias(n_vocab, n_bias, bias_data))

        # 2. Apply repetition, frequency, and presence penalties.
        has_penalty = (repeat_penalty != 1.0 or frequency_penalty != 0.0 or presence_penalty != 0.0)
        if has_penalty:
            # llama.cpp manages the history rings.
            llama_sampler_chain_add(self.ptr, llama_sampler_init_penalties(
                penalty_last_n, repeat_penalty, frequency_penalty, presence_penalty
            ))

        # 3. Apply sampling filters in order.
        if temperature > 0:
            if top_k > 0:
                llama_sampler_chain_add(self.ptr, llama_sampler_init_top_k(top_k))
            if top_p < 1.0:
                llama_sampler_chain_add(self.ptr, llama_sampler_init_top_p(top_p, 1))
            if 0.0 < min_p < 1.0:
                llama_sampler_chain_add(self.ptr, llama_sampler_init_min_p(min_p, 1))
            
            llama_sampler_chain_add(self.ptr, llama_sampler_init_temp(temperature))
            llama_sampler_chain_add(self.ptr, llama_sampler_init_dist(seed))
        else:
            llama_sampler_chain_add(self.ptr, llama_sampler_init_greedy())

        self._neg_inf = -1e10

    def accept(self, token_id: int):
        """Accept a token into native sampler history."""
        if self.ptr:
            llama_sampler_accept(self.ptr, token_id)

    def sample(self, ctx, idx=-1, limit_start=None, limit_end=None, allow_tokens=None):
        """Sample a token with optional range and allow-list constraints."""
        ctx_ptr = ctx.ptr if hasattr(ctx, 'ptr') else ctx
            
        # Apply range and allow-list constraints to logits.
        if (limit_start is not None or limit_end is not None) and hasattr(ctx, 'get_logits'):
            n_vocab = llama_vocab_n_tokens(ctx.model.vocab)
            logits_ptr = ctx.get_logits_ith(idx) # Get logits at the requested index.
            logits = np.ctypeslib.as_array(logits_ptr, shape=(n_vocab,))
            
            s = max(0, limit_start) if limit_start is not None else 0
            e = min(n_vocab, limit_end) if limit_end is not None else n_vocab
            
            # Mask everything except the specified range and allow-list.
            mask = np.ones(n_vocab, dtype=bool)
            mask[s:e] = False # Retain tokens within the range.
            if allow_tokens:
                for t in allow_tokens:
                    if 0 <= t < n_vocab:
                        mask[t] = False # Retain allow-listed tokens.
            
            logits[mask] = self._neg_inf
        
        # Sample through the native chain.
        # llama_sampler_sample automatically calls accept() on the chain.
        return llama_sampler_sample(self.ptr, ctx_ptr, idx)

    def free(self):
        """Release sampler resources."""
        if hasattr(self, 'ptr') and self.ptr:
            llama_sampler_free(self.ptr)
            self.ptr = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.free()

    def __del__(self):
        self.free()


class ASRStreamDecoder:
    """Decode streaming ASR bytes and report through ASRReporter."""
    def __init__(self, vocab, reporter=None):
        self.vocab = vocab
        self.reporter = reporter
        self.byte_decoder = codecs.getincrementaldecoder("utf-8")(errors='replace')
        self.generated_text = ""
        self.tokens_generated = 0
        self.tokens = []

    def push(self, token_id: int):
        """Push a token and return newly decoded text."""
        raw_bytes = token_to_bytes(self.vocab, token_id)
        text_piece = self.byte_decoder.decode(raw_bytes, final=False)
        self.tokens.append(text_piece)
        self.tokens_generated += 1
        
        self.generated_text += text_piece
        
        if self.reporter:
            self.reporter.stream(text_piece)
            
        return text_piece

    def flush(self):
        """Flush and return remaining bytes."""
        remaining = self.byte_decoder.decode(b"", final=True)
        self.tokens.append(remaining)
        self.generated_text += remaining
        return remaining





# =========================================================================
# Embedding Table
# =========================================================================



class LlamaEmbeddingTable:
    """Dequantize embedding rows on demand through table[ids]."""
    def __init__(self, raw_data, qtype):
        self.raw_data = raw_data
        self.qtype = qtype
        
    def __len__(self):
        return self.raw_data.shape[0]

    def __getitem__(self, tokens):
        from gguf.quants import dequantize
        
        # Return native floating-point data directly.
        if self.raw_data.dtype in (np.float32, np.float16):
            return self.raw_data[tokens].astype(np.float32)
            
        # Use the backend library for dequantization.
        return dequantize(self.raw_data[tokens], self.qtype.value)

def _skip_gguf_value(mm, offs, v_type):
    # UINT8=0, INT8=1, UINT16=2, INT16=3, UINT32=4, INT32=5, FLOAT32=6, BOOL=7, STRING=8, ARRAY=9, UINT64=10, INT64=11, FLOAT64=12
    fixed = [1, 1, 2, 2, 4, 4, 4, 1, -1, -2, 8, 8, 8]
    val_len = fixed[v_type]
    if val_len > 0:
        return offs + val_len
    elif val_len == -1: # string
        slen = struct.unpack_from("<Q", mm, offs)[0]
        return offs + 8 + slen
    elif val_len == -2: # array
        itype, alen = struct.unpack_from("<IQ", mm, offs)
        offs += 12
        if itype == 8: # string array
            for _ in range(alen):
                slen = struct.unpack_from("<Q", mm, offs)[0]
                offs += 8 + slen
        else:
            item_len = fixed[itype]
            if item_len > 0:
                offs += item_len * alen
            else:
                raise ValueError(Notice('validation.llama.nested_arrays_or_unknown_type_not_supported_in'))
        return offs

def get_token_embeddings_gguf(model_path, target_tensor="token_embd.weight"):
    """
    Extract GGUF embeddings by binary offsets.
    Avoid loading the entire model or constructing the tokenizer vocabulary.
    """
    t_start = time.time()
    mm = np.memmap(model_path, mode='r')
    
    # Read the file header.
    tensor_count, kv_count = struct.unpack_from("<QQ", mm, 8)
    offs = 24
    alignment = 32
    
    # Scan or skip metadata fields.
    for _ in range(kv_count):
        key_len = struct.unpack_from("<Q", mm, offs)[0]
        offs += 8
        if key_len == 17 and mm[offs:offs+17].tobytes() == b'general.alignment':
            offs += 17
            v_type = struct.unpack_from("<I", mm, offs)[0]
            offs += 4
            if v_type == 4: # UINT32
                alignment = struct.unpack_from("<I", mm, offs)[0]
                offs += 4
                continue
        else:
            offs += key_len
            
        v_type = struct.unpack_from("<I", mm, offs)[0]
        offs += 4
        offs = _skip_gguf_value(mm, offs, v_type)
        
    # Find the requested tensor in tensor metadata.
    target_rel_offset = None
    target_type = None
    target_shape = None # GGUF stores shape in reverse order: [n_embd, vocab_size].
    
    target_bytes = target_tensor.encode('utf-8')
    for _ in range(tensor_count):
        name_len = struct.unpack_from("<Q", mm, offs)[0]
        offs += 8
        is_target = False
        if name_len == len(target_bytes) and mm[offs:offs+name_len].tobytes() == target_bytes:
            is_target = True
        offs += name_len
        
        n_dims = struct.unpack_from("<I", mm, offs)[0]
        offs += 4
        
        shape = struct.unpack_from(f"<{n_dims}Q", mm, offs) # Return the tuple.
        offs += 8 * n_dims
        
        t_type = struct.unpack_from("<I", mm, offs)[0]
        offs += 4
        
        rel_offset = struct.unpack_from("<Q", mm, offs)[0]
        offs += 8
        
        if is_target:
            target_shape = shape
            target_type = t_type
            target_rel_offset = rel_offset
            
    # Locate the data section and load the tensor.
    padding = offs % alignment
    if padding != 0:
        offs += (alignment - padding)
    data_offset = offs
    
    if target_shape is None:
        logger.error(Notice('diagnostic.llama.cannot_find_in', value0=model_path, value1=target_tensor))
        return None
        
    abs_offset = data_offset + target_rel_offset
    n_embd = target_shape[0]     # Embedding dimension.
    vocab_size = target_shape[1] # Vocabulary size.
    
    qtype = GGMLQuantizationType(target_type)
    if qtype in GGML_QUANT_SIZES:
        block_size, type_size = GGML_QUANT_SIZES[qtype]
        bytes_per_row = (n_embd // block_size) * type_size
    else:
        # F32 or F16.
        if qtype == GGMLQuantizationType.F32:
            bytes_per_row = n_embd * 4
        elif qtype == GGMLQuantizationType.F16:
            bytes_per_row = n_embd * 2
        else:
            raise ValueError(Notice('validation.llama.unsupported_data_format', value0=qtype.name))

    total_bytes = vocab_size * bytes_per_row
    raw_data = mm[abs_offset : abs_offset + total_bytes]
    
    if qtype in (GGMLQuantizationType.F32, GGMLQuantizationType.F16):
        if qtype == GGMLQuantizationType.F32:
            raw_data = raw_data.view(np.float32).reshape(vocab_size, n_embd)
        else:
            raw_data = raw_data.view(np.float16).reshape(vocab_size, n_embd)
    else:
        raw_data = raw_data.reshape(vocab_size, bytes_per_row)
        
    total_time = time.time() - t_start
    logger.info(Notice('diagnostic.llama.qwenasr_embedding_view_loaded_ms', value0=total_time * 1000))
    logger.info(Notice('diagnostic.llama.quantization_dims_tokens', value0=qtype.name, value1=n_embd, value2=vocab_size))
    
    return LlamaEmbeddingTable(raw_data, qtype)



# =========================================================================
# Utilities
# =========================================================================


def text_to_tokens(vocab, text, add_special=False, parse_special=True):
    text_bytes = text.encode("utf-8")
    n_tokens_max = len(text_bytes) + 32
    tokens = (llama_token * n_tokens_max)()
    n = llama_tokenize(vocab, text_bytes, len(text_bytes), tokens, n_tokens_max, add_special, parse_special)
    return [tokens[i] for i in range(n)] if n >= 0 else []

def token_to_bytes(vocab, token_id):
    buf = ctypes.create_string_buffer(256)
    n = llama_token_to_piece(vocab, token_id, buf, ctypes.sizeof(buf), 0, True)
    return buf.raw[:n] if n > 0 else b""
