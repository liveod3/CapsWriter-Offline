# coding: utf-8
"""
模型检查模块

检查配置的语音模型文件是否存在，如果不存在则提供下载链接。
"""

from core.i18n import Notice, tr

import sys
from pathlib import Path

from config_server import ServerConfig as Config
from config_server import ModelPaths, ModelDownloadLinks
from core.server.state import console
from . import logger



def check_model(*, interactive=True) -> None:
    """
    根据配置的模型类型检查所需的模型文件是否存在
    
    如果模型文件不存在，显示错误信息和下载链接后退出程序。
    
    Raises:
        SystemExit: 当模型类型不支持或模型文件缺失时退出
    """
    model_type = Config.model_type.lower()
    logger.debug(Notice('diagnostic.check_model.checking_model_files_type', value0=model_type))

    # 根据模型类型确定需要检查的文件
    if model_type == 'fun_asr_nano':
        model_dir = ModelPaths.fun_asr_nano_gguf_dir
        required_files = [
            ModelPaths.fun_asr_nano_gguf_encoder_adaptor,
            ModelPaths.fun_asr_nano_gguf_ctc,
            ModelPaths.fun_asr_nano_gguf_llm_decode,
            ModelPaths.fun_asr_nano_gguf_token,
        ]
    elif model_type == 'sensevoice':
        model_dir = ModelPaths.sensevoice_dir
        required_files = [
            ModelPaths.sensevoice_encoder,
            ModelPaths.sensevoice_decoder,
            ModelPaths.sensevoice_tokenizer,
        ]
    elif model_type == 'paraformer':
        model_dir = ModelPaths.paraformer_dir
        required_files = [
            ModelPaths.paraformer_model,
            ModelPaths.paraformer_tokens,
        ]
    elif model_type == 'qwen_asr':
        model_dir = ModelPaths.qwen3_asr_gguf_dir
        required_files = [
            ModelPaths.qwen3_asr_gguf_encoder_frontend,
            ModelPaths.qwen3_asr_gguf_encoder_backend,
            ModelPaths.qwen3_asr_gguf_llm_decode,
        ]
    else:
        logger.error(Notice('engine.unsupported_model', model=Config.model_type))
        console.print(tr('model.unsupported', value0=Config.model_type), style='bright_red')
        if interactive:
            input(tr('model.exit'))
        sys.exit(1)

    # 检查所有必需的文件
    missing_files = []
    for file_path in required_files:
        if not file_path.exists():
            missing_files.append(file_path)
            logger.warning(Notice('diagnostic.check_model.model_file_missing_bold_yellow_bold_yellow', value0=file_path))

    # 如果有缺失的文件，显示错误信息并提供下载链接
    if missing_files:
        logger.error(Notice('diagnostic.check_model.model_file_verification_failed_files_missing', value0=len(missing_files)))
        error_msg = tr('model.missing')
        error_msg += tr('model.type', value0=model_type)
        for file_path in missing_files:
            error_msg += tr('model.file_missing', value0=file_path.name)

        # 检查是否有文件被错放到上级目录
        for parent in model_dir.parents:
            if any((parent / fp.name).exists() for fp in missing_files):
                error_msg += tr('model.parent_folder', value0=model_dir)
                break

        # 提供统一下载页面链接
        error_msg += tr('model.download')
        error_msg += f'[cyan]{ModelDownloadLinks.models_page}[/cyan]\n\n'

        error_msg += tr('model.extract', value0=ModelPaths.model_dir)
        error_msg += '\n'
        
        logger.error(Notice('diagnostic.check_model.model_files_are_missing_count'), len(missing_files),
                     extra={'console_handled': True})
        console.print(error_msg)
        if interactive:
            input(tr('model.exit'))
        sys.exit(1)

    # 所有必需文件检查通过
    logger.info(Notice('diagnostic.check_model.model_files_verified', value0=model_type))
    console.print(tr('model.checked', value0=model_type), end='\n\n')
