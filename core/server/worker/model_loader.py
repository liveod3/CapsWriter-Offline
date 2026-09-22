# coding: utf-8
"""
识模型加载模块

负责 ASR 引擎和标点模型的实例化，支持多种后端引擎的一致性加载。
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
    模型加载器
    
    负责 ASR 引擎和辅助模型（标点、对齐器）的生命周期管理。
    自动根据引擎能力挂载补丁插件。
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
        加载模型资源
        
        逻辑流程：
        1. 加载 ASR 核心引擎（通过工厂模式）
        2. 扫描引擎能力 (Capabilities)
        3. 自适应挂载缺失能力的插件 (Punc, Aligner)
        """
        # 1. 延迟导入通用库
        # Spawned workers use the retained server preference, not the module default.
        set_language(getattr(Config, 'ui_language', 'auto'))
        with console.status(tr('server.loading_modules'), spinner="bouncingBall", spinner_style="yellow"):
            import sherpa_onnx
        
        t1 = time.time()
        model_type = Config.model_type.lower()
        logger.info(Notice('diagnostic.model_loader.initializing_speech_system_engine', value0=model_type))

        try:
            # 2. 通过工厂实例化 ASR 核心引擎
            self.recognizer = EngineFactory.create_asr_engine(model_type)
            caps = self.recognizer.capabilities
            logger.info(Notice('diagnostic.model_loader.engine_loaded_capabilities', value0=[c.name for c in caps]))

            # 3. 智能补丁：如果引擎不自带标点能力，则挂载标点模型
            if EngineCapabilities.PUNC not in caps:
                self._load_punc_model()

            # 4. 智能补丁：如果引擎不自带时间戳能力，则挂载对齐器插件
            if EngineCapabilities.TIMESTAMPS not in caps:
                self._load_align_model()


            logger.info(Notice('diagnostic.model_loader.speech_system_initialized_in_s', value0=time.time() - t1))
            
        except Exception as e:
            logger.error(Notice('diagnostic.model_loader.model_loading_failed_error'), type(e).__name__)
            raise e

    def _load_punc_model(self):
        """加载标点补足模型插件"""
        logger.info(Notice('diagnostic.model_loader.engine_lacks_punctuation_support_attaching_puncengine'))
        self.punc_model = EngineFactory.create_punc_engine()

    def _load_align_model(self):
        """挂载独立 Aligner 进程的远程代理。"""
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
        """释放模型资源"""
        if self.recognizer and hasattr(self.recognizer, 'cleanup'):
            self.recognizer.cleanup()
        if self.aligner and hasattr(self.aligner, 'cleanup'):
            self.aligner.cleanup()
        self.recognizer = None
        self.punc_model = None
        self.aligner = None
