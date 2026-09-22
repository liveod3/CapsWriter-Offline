# coding: utf-8

from core.i18n import Notice
from typing import Any, Dict, Type, Optional
from .base import BaseASREngine, BasePuncEngine, BaseAlignEngine
from config_server import (
    ServerConfig as Config,
    ParaformerArgs, SenseVoiceArgs, 
    FunASRNanoGGUFArgs, Qwen3ASRGGUFArgs,
    ModelPaths, ForceAlignerGGUFArgs
)


class EngineFactory:
    """
    引擎工厂类
    
    统一管理所有识别引擎、标点引擎和对齐引擎的实例化逻辑。
    使用延迟加载模式避免启动过慢。
    """

    # --- ASR 引擎加载器 ---

    @staticmethod
    def _load_sensevoice():
        from .sensevoice_onnx.asr_engine import SenseVoiceEngine, SenseVoiceConfig
        return SenseVoiceEngine, SenseVoiceConfig, SenseVoiceArgs

    @staticmethod
    def _load_paraformer():
        from .paraformer_onnx.asr_engine import ParaformerEngine, ParaformerConfig
        return ParaformerEngine, ParaformerConfig, ParaformerArgs

    @staticmethod
    def _load_fun_asr_nano():
        from .fun_asr_gguf.asr_engine import FunASREngine, ASREngineConfig as FunASRConfig
        return FunASREngine, FunASRConfig, FunASRNanoGGUFArgs

    @staticmethod
    def _load_qwen_asr():
        from .qwen_asr_gguf.asr_engine import QwenASREngine, ASREngineConfig as QwenASRConfig
        return QwenASREngine, QwenASRConfig, Qwen3ASRGGUFArgs

    _ASR_LOADERS = {
        'sensevoice': _load_sensevoice,
        'paraformer': _load_paraformer,
        'fun_asr_nano': _load_fun_asr_nano,
        'qwen_asr': _load_qwen_asr
    }

    @staticmethod
    def create_asr_engine(model_type: str) -> BaseASREngine:
        """创建 ASR 核心引擎"""
        model_type = model_type.lower()
        if model_type not in EngineFactory._ASR_LOADERS:
            raise ValueError(Notice('validation.factory.enginefactory_unsupported_asr_type', value0=model_type))

        loader = EngineFactory._ASR_LOADERS[model_type]
        EngineClass, ConfigClass, ArgsObj = loader()
        
        # 旧本机配置中已移除功能的字段不再传入引擎。
        from dataclasses import fields
        accepted = {field.name for field in fields(ConfigClass)}
        config_data = {k: v for k, v in ArgsObj.__dict__.items() if k in accepted}
        config = ConfigClass(**config_data)
        
        return EngineClass(config)

    # --- 辅助引擎加载器 ---

    @staticmethod
    def create_punc_engine() -> BasePuncEngine:
        """创建标点引擎 (目前使用 CT-Transformer)"""
        try:
            from .ct_transformer.punc_engine import CTTransformerPuncEngine
            model_path = ModelPaths.punc_model_dir.as_posix()
            return CTTransformerPuncEngine(model_path)
        except Exception as e:
            from . import logger
            logger.warning(Notice('diagnostic.factory.punctuation_model_unavailable_continuing_without_punctuation_error'), type(e).__name__)
            return BasePuncEngine(None)

    @staticmethod
    def create_align_engine() -> BaseAlignEngine:
        """创建对齐引擎 (目前使用 Qwen Force Aligner)"""
        try:
            from .force_aligner_gguf.align_engine import QwenForceAligner, AlignerConfig
            align_cfg_data = {
                k: v for k, v in ForceAlignerGGUFArgs.__dict__.items() 
                if not k.startswith('_')
            }
            config = AlignerConfig(**align_cfg_data)
            return QwenForceAligner(config)
        except Exception as e:
            from . import logger
            logger.warning(Notice('engine.alignment_missing', error=type(e).__name__))
            # 检查模型文件是否错放到上级目录
            aligner_dir = ModelPaths.force_aligner_gguf_dir
            aligner_files = [
                ModelPaths.force_aligner_gguf_encoder_frontend,
                ModelPaths.force_aligner_gguf_encoder_backend,
                ModelPaths.force_aligner_gguf_llm_decode,
            ]
            for parent in aligner_dir.parents:
                if any((parent / fp.name).exists() for fp in aligner_files):
                    logger.warning(Notice('engine.alignment_directory', directory=aligner_dir))
                    break
            return BaseAlignEngine(None)
