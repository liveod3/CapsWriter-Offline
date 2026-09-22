
from core.i18n import Notice, tr

import os
import time
from pathlib import Path

from . import llama
from .encoder import AudioEncoder
from .ctc_decoder import CTCDecoder
from .utils import vprint, timer
from .prompt_builder import PromptBuilder
from .schema import ASREngineConfig

class Models:
    """Manage model components."""
    
    def __init__(self, config: ASREngineConfig):
        self.config = config
        verbose = self.config.verbose
        
        # Runtime components.
        self.encoder = None
        self.ctc_decoder = None
        
        # Decoder components.
        self.model = None
        self.ctx = None
        self.vocab = None
        self.eos_token = None
        self.embedding_table = None
        
        # Auxiliary components.
        self.prompt_builder = None
        
        self._initialized = False

        try:
            _, elapsed = timer(self._load_models, verbose)
            vprint(Notice('terminal.models.model_loaded_elapsed_s', value0=elapsed), verbose)
            self._initialized = True
        except Exception as e:
            vprint(Notice('terminal.models.model_initialization_failed_error', value0=type(e).__name__), verbose)
            raise RuntimeError(Notice('validation.models.model_initialization_failed', value0=type(e).__name__)) from None

    def _load_models(self, verbose):
        """Load model components."""
        # 1. Encoder (ONNX)
        vprint(Notice('terminal.models.loading_audio_encoder'), verbose)
        self.encoder = AudioEncoder(
            model_path=self.config.encoder_onnx_path,
            onnx_provider=self.config.onnx_provider,
            dml_pad_to=self.config.dml_pad_to
        )

        # 2. CTC Decoder (ONNX + Search)
        vprint(Notice('terminal.models.loading_ctc_decoder'), verbose)
        self.ctc_decoder = CTCDecoder(
            model_path=self.config.ctc_onnx_path,
            tokens_path=self.config.tokens_path,
            onnx_provider=self.config.onnx_provider,
            dml_pad_to=self.config.dml_pad_to,
        )

        # 3. GGUF LLM Decoder
        vprint(Notice('terminal.models.loading_gguf_llm_decoder'), verbose)
        if self.config.vulkan_force_fp32:
            os.environ["GGML_VK_DISABLE_F16"] = "1" 
        self.model = llama.LlamaModel(self.config.decoder_gguf_path, use_gpu=self.config.llm_use_gpu)
        self.vocab = self.model.vocab
        self.eos_token = self.model.eos_token

        # 4. Embeddings
        vprint(Notice('terminal.models.loading_embedding_weights'), verbose)
        self.embedding_table = llama.get_token_embeddings_gguf(self.config.decoder_gguf_path)
        
        # 5. LLM Context
        vprint(Notice('terminal.models.creating_llm_context'), verbose)
        self.ctx = llama.LlamaContext(
            self.model,
            n_ctx=2048,
            n_batch=2048,
            n_ubatch=self.config.n_ubatch,
            n_threads=self.config.n_threads,
        )
        
        # 6. Prompt builder.
        vprint(Notice('terminal.models.initializing_prompt_builder'), verbose)
        self.prompt_builder = PromptBuilder(self.vocab, self.embedding_table)

    def cleanup(self):
        self.ctx = None
        self.model = None
        self.encoder = None
        self.ctc_decoder = None
        self._initialized = False
        print(tr('terminal.models.asr_resources_released'))
