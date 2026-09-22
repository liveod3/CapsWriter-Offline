
from core.i18n import tr
import time
import re
import ctypes
import numpy as np
from typing import List, Tuple, Optional, Dict, Any

from . import logger
from . import llama
from .ctc_decoder import CTCDecoder
from .utils import vprint, timer
from .schema import DecodeResult, Timings, RecognitionStream, LLMDecodeResult
from .display import DisplayReporter
from .models import Models
from .ctc_aligner import CTCAligner
from .llm_decoder import LLMDecoder

# 全局静默 Reporter，用于默认参数，避免重复创建线程
_SILENT_REPORTER = DisplayReporter(verbose=False)

class InferencePipeline:
    """ASR 核心指挥者 (Conductor)：负责调度音频编码、CTC 解码、Prompt 构建及 LLM 推理等细粒度组件"""
    def __init__(self, models: Models):
        self.models = models
        self.llm_decoder = LLMDecoder(models)

    def create_stream(self) -> RecognitionStream:
        """创建识别流"""
        return RecognitionStream(sample_rate=self.models.config.sample_rate)

    def decode_stream(
        self,
        stream: RecognitionStream,
        language: Optional[str] = None,
        context: Optional[str] = None,
        verbose: bool = True,
        reporter: Optional[DisplayReporter] = None,
        temperature: float = 0.3,
        top_p: float = 1.0,
        top_k: int = 50,
        timestamp_offset: float = -0.24
    ) -> DecodeResult:
        
        reporter = reporter or _SILENT_REPORTER
        timings = Timings()

        # 0. 检查原始音频数据长度，空音频防御
        if len(stream.audio_data) < 1600:
            return DecodeResult(text="", timings=timings)
        
        # 1. Encode
        reporter.print(tr('terminal.pipeline.encoding_audio'))
        (audio_embd, enc_output), timings.encode = timer(self.models.encoder.encode, stream.audio_data)
        reporter.print(tr('terminal.pipeline.elapsed_ms', value0=timings.encode * 1000))

        # 2. CTC Decoding
        reporter.print(tr('terminal.pipeline.decoding_ctc'))
        (ctc_results, ctc_times), timings.ctc = timer(
            self.models.ctc_decoder.decode,
            enc_output, 
            self.models.config.enable_ctc, 
        )
        reporter.print(tr('terminal.pipeline.ctc', value0=''.join([r.text for r in ctc_results])))
        t_detail = " | ".join([f"{k}:{v*1000:.1f}ms" for k, v in ctc_times.items() if v > 0])
        reporter.print(tr('terminal.pipeline.elapsed_ms_details', value0=timings.ctc * 1000, value1=t_detail))

        # 3. Prompt Builder
        reporter.print(tr('terminal.pipeline.preparing_prompt'))
        (p_embd, s_embd, n_p, n_s, p_text), timings.prepare = timer(
            self.models.prompt_builder.build_prompt, language, context
        )
        reporter.print(tr('terminal.pipeline.prefix_tokens', value0=n_p))
        reporter.print(tr('terminal.pipeline.suffix_tokens', value0=n_s))

        # 4. LLM Decoding Loop
        reporter.print(tr('terminal.pipeline.decoding_llm'))
        reporter.print("=" * 70)
        full_embd = np.concatenate([p_embd, audio_embd.astype(np.float32), s_embd], axis=0)
        n_input_tokens = full_embd.shape[0]

        # 5. LLM 解码循环：若熔断则加温重试（总共最多解码7次，最后的温度是2.1）
        llm_res = None
        current_temp = temperature
        for retry_idx in range(7):
            if retry_idx > 0:
                print(tr('terminal.pipeline.g_decoding_failed_retrying_temperature_retry', value0=current_temp, value1=retry_idx))
            llm_res = self.llm_decoder.decode(
                full_embd, n_input_tokens, self.models.config.n_predict, 
                stream_output=verbose, reporter=reporter,
                temperature=current_temp, top_p=top_p, top_k=top_k
            )
            if not llm_res.is_aborted: break    # 正常解码就跳出循环
            llm_res.text += "====解码有误，强制熔断===="
            current_temp += 0.3
        text = llm_res.text.strip()
        timings.inject = llm_res.t_inject
        timings.llm_generate = llm_res.t_gen
        
        if reporter: reporter.print("\n" + "=" * 70)

        # 6. Timestamp Alignment
        reporter.print(tr('terminal.pipeline.aligning_timestamps'))
        aligned, timings.align = timer(CTCAligner.align, ctc_results, text, timestamp_offset=timestamp_offset)
        tokens = [seg[0] for seg in aligned]
        timestamps = [seg[1] for seg in aligned]

        reporter.print(tr('terminal.pipeline.alignment_time_ms', value0=timings.align * 1000))
        preview = " ".join([f"{r[0]}({r[1]:.2f}s)" for r in aligned[:10]])
        if len(aligned) > 10: preview += " ..."
        reporter.print(tr('terminal.pipeline.result_preview', value0=preview))

        # Set stream result
        stream.set_result(text=text, timestamps=timestamps, tokens=tokens)
        
        return DecodeResult(
            text=text, ctc_results=ctc_results, aligned=aligned,
            audio_embd=audio_embd, n_prefix=n_p, n_suffix=n_s,
            n_gen=llm_res.n_gen, timings=timings,
            is_aborted=llm_res.is_aborted
        )


