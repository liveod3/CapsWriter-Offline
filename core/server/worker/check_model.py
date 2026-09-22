# coding: utf-8
"""
Model validation.

Check configured model files and provide download links when missing.
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
    Check required files for the configured engine.
    
    Report missing files and download links, then exit.
    
    Raises:
        SystemExit: Unsupported engine or missing model files.
    """
    model_type = Config.model_type.lower()
    logger.debug(Notice('diagnostic.check_model.checking_model_files_type', value0=model_type))

    # Select required files by engine.
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

    # Check all required files.
    missing_files = []
    for file_path in required_files:
        if not file_path.exists():
            missing_files.append(file_path)
            logger.warning(Notice('diagnostic.check_model.model_file_missing_bold_yellow_bold_yellow', value0=file_path))

    # Report missing files with download information.
    if missing_files:
        logger.error(Notice('diagnostic.check_model.model_file_verification_failed_files_missing', value0=len(missing_files)))
        error_msg = tr('model.missing')
        error_msg += tr('model.type', value0=model_type)
        for file_path in missing_files:
            error_msg += tr('model.file_missing', value0=file_path.name)

        # Check for files placed one directory too high.
        for parent in model_dir.parents:
            if any((parent / fp.name).exists() for fp in missing_files):
                error_msg += tr('model.parent_folder', value0=model_dir)
                break

        # Link to the shared model download page.
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

    # All required files are present.
    logger.info(Notice('diagnostic.check_model.model_files_verified', value0=model_type))
    console.print(tr('model.checked', value0=model_type), end='\n\n')
