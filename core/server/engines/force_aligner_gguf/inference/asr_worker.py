# coding=utf-8

from core.i18n import Notice, tr
import os
from .. import logger
from .schema import MsgType, StreamingMessage, ASREngineConfig
from .encoder import QwenAudioEncoder
from .aligner import QwenForcedAligner

def do_encode_task(msg, encoder, from_enc_q):
    """Handle an audio encoding request."""
    audio_embd, encode_time, = encoder.encode(msg.data)
    from_enc_q.put(StreamingMessage(
        msg_type=MsgType.MSG_EMBD, 
        data=audio_embd, 
        is_last=msg.is_last, 
        encode_time=encode_time,
    ))

def do_align_task(msg, aligner, from_align_q):
    """Handle a timestamp alignment request."""
    if aligner is None:
        from_align_q.put(StreamingMessage(MsgType.MSG_ALIGN, data=None))
        return

    try:
        res = aligner.align(
            msg.data, 
            msg.text, 
            language=msg.language, 
            offset_sec=msg.offset_sec
        )
        from_align_q.put(StreamingMessage(
            msg_type=MsgType.MSG_ALIGN, 
            data=res, 
            is_last=msg.is_last
        ))
    except Exception as e:
        print(tr('terminal.asr_worker.asrworker_alignment_failed', value0=e))
        from_align_q.put(StreamingMessage(MsgType.MSG_ALIGN, data=None))

def asr_helper_worker_proc(to_worker_q, from_enc_q, from_align_q, config: ASREngineConfig):
    """Process helper tasks synchronously with separate encoding and alignment reply queues."""
    
    # 1. Initialize resources.
    try:
        # Split Model Paths
        frontend_path = os.path.join(config.model_dir, config.encoder_frontend_fn)
        backend_path = os.path.join(config.model_dir, config.encoder_backend_fn)
        
        # Initialize the split encoder.
        encoder = QwenAudioEncoder(
            frontend_path=frontend_path,
            backend_path=backend_path,
            use_dml=config.use_dml,
            pad_to=config.pad_to,
            verbose=config.verbose
        )
        
        aligner = None
        if config.enable_aligner and config.align_config:
            from .aligner import QwenForcedAligner
            aligner = QwenForcedAligner(
                config.align_config
            )
            
        from_enc_q.put(StreamingMessage(MsgType.MSG_READY))
    except Exception as e:
        logger.error(Notice('diagnostic.asr_worker.asrworker_model_loading_failed', value0=e))
        from_enc_q.put(StreamingMessage(MsgType.MSG_ERROR, data=e))
        return

    # 2. Process incoming tasks.
    while True:
        msg: StreamingMessage = to_worker_q.get()
        
        if msg.msg_type == MsgType.CMD_STOP:
            from_enc_q.put(StreamingMessage(MsgType.MSG_DONE))
            from_align_q.put(StreamingMessage(MsgType.MSG_DONE))
            break
            
        if msg.msg_type == MsgType.CMD_ENCODE:
            do_encode_task(msg, encoder, from_enc_q)
            
        elif msg.msg_type == MsgType.CMD_ALIGN:
            do_align_task(msg, aligner, from_align_q)
