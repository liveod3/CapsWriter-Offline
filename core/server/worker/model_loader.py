# coding: utf-8
"""
Model loading.

Construct ASR and punctuation backends through a shared interface.
"""

from core.i18n import Notice, set_language, tr

import time
from core.server.state import console
from config_server import (
    ServerConfig as Config, 
    ModelPaths
)
from ..engines.factory import EngineFactory
from ..engines.base import EngineCapabilities
from . import logger


class ModelLoader:
    """
    Model loader.
    
    Manage ASR and auxiliary punctuation/alignment resources.
    Attach auxiliary engines according to ASR capabilities.
    """
    def __init__(self, align_queue_in=None, align_queue_out=None, failure_event=None):
        self.recognizer = None
        self.punc_model = None
        self.aligner = None
        self.align_queue_in = align_queue_in
        self.align_queue_out = align_queue_out
        self.failure_event = failure_event

    def load(self):
        """
        Load model resources.
        
        Loading sequence:
        1. Construct the ASR engine through the factory.
        2. Inspect its capabilities.
        3. Attach missing punctuation or alignment support.
        """
        # 1. Import shared libraries lazily.
        # Spawned workers use the retained server preference, not the module default.
        set_language(getattr(Config, 'ui_language', 'auto'))
        with console.status(tr('server.loading_modules'), spinner="bouncingBall", spinner_style="yellow"):
            import sherpa_onnx
        
        t1 = time.time()
        model_type = Config.model_type.lower()
        logger.info(Notice('diagnostic.model_loader.initializing_speech_system_engine', value0=model_type))

        try:
            # 2. Construct the ASR engine through the factory.
            self.recognizer = EngineFactory.create_asr_engine(model_type)
            caps = self.recognizer.capabilities
            logger.info(Notice('diagnostic.model_loader.engine_loaded_capabilities', value0=[c.name for c in caps]))

            # 3. Attach punctuation if the engine lacks it.
            if EngineCapabilities.PUNC not in caps:
                self._load_punc_model()

            # 4. Attach alignment if the engine lacks timestamps.
            if EngineCapabilities.TIMESTAMPS not in caps:
                self._load_align_model()


            logger.info(Notice('diagnostic.model_loader.speech_system_initialized_in_s', value0=time.time() - t1))
            
        except Exception as e:
            logger.error(Notice('diagnostic.model_loader.model_loading_failed_error'), type(e).__name__)
            raise e

    def _load_punc_model(self):
        """Load the auxiliary punctuation model."""
        logger.info(Notice('diagnostic.model_loader.engine_lacks_punctuation_support_attaching_puncengine'))
        self.punc_model = EngineFactory.create_punc_engine()

    def _load_align_model(self):
        """Attach the remote proxy for the independent aligner process."""
        from ..engines.manager import ProcessAlignerProxy
        if self.align_queue_in is None or self.align_queue_out is None:
            raise RuntimeError(Notice('validation.model_loader.aligner_process_queues_are_not_initialized'))
        timeout = getattr(Config, 'aligner_request_timeout', 60)
        logger.info(Notice('diagnostic.model_loader.engine_lacks_timestamps_attached_separate_aligner_process_proxy', value0=timeout))
        self.aligner = ProcessAlignerProxy(
            self.align_queue_in,
            self.align_queue_out,
            timeout_sec=timeout,
            failure_event=self.failure_event,
        )

    def cleanup(self):
        """Release model resources."""
        if self.recognizer and hasattr(self.recognizer, 'cleanup'):
            self.recognizer.cleanup()
        if self.aligner and hasattr(self.aligner, 'cleanup'):
            self.aligner.cleanup()
        self.recognizer = None
        self.punc_model = None
        self.aligner = None
