# coding: utf-8
"""
Recognition pipeline.

Recognize, deduplicate, and merge audio segments using two strategies:
1. text: Text overlap matching independent of timestamps.
2. text_accu: Timestamp-aware deduplication for subtitles.
"""

from core.i18n import Notice
from core.logger import log_content, diagnostic_event

import re
import time
from core.server.state import WorkerState
from core.server.schema import Task, Result
from core.server.formatter import TextFormatter
from config_server import ServerConfig as Config
from core.tools.token_sync import sync_tokens_from_text
from core.server.engines.base import EngineCapabilities
from .audio import process_audio_task
from . import logger

# Import merge algorithms.
from core.server.merger import (
    merge_by_text,
    merge_tokens_by_sequence_matcher,
    process_tokens_safely,
    tokens_to_text,
)


class TaskPipeline:
    """
    Recognition pipeline.
    
    Coordinate ASR, punctuation, and alignment engines.
    Select stages according to source (mic/file) and engine capabilities.
    """

    def __init__(self, recognizer, punc_model=None, aligner=None, state: WorkerState = None):
        self.recognizer = recognizer
        self.punc_model = punc_model
        self.aligner = aligner
        self.formatter = TextFormatter(punc_model)
        self.state = state or WorkerState()

    def _process_simple_merge(self, result: Result, stream_result_text: str) -> None:
        """Merge primary text for dictation output."""
        try:
            segment_text = stream_result_text.replace('@@', '').strip()
            segment_text = re.sub(r'\s+', ' ', segment_text)
            
            prev_len = len(result.text)
            result.text = merge_by_text(result.text, segment_text)
            added_chars = len(result.text) - prev_len
            
            logger.debug(Notice('diagnostic.pipeline.appending_text_chars_segment_total', value0=added_chars, value1=len(segment_text), value2=len(result.text)))
        except Exception as e:
            logger.warning(Notice('diagnostic.pipeline.text_merge_failed_error'), type(e).__name__)

    def process(self, task: Task) -> Result:
        """
        Process one audio segment and return its result.
        """
        try:
            logger.info(Notice('diagnostic.pipeline.recognition_started_task_source'), task.task_id[:8], task.type)
            diagnostic_event(logger, 'asr.segment_started', task_id=task.task_id,
                             socket_id=task.socket_id, source=task.type, offset=task.offset)
            is_first_segment = task.key not in self.state.sessions
            session = self.state.get_session(task.task_id, task.socket_id, task.type)
            result = session.result

            # Refresh GPU activity whenever a task arrives.
            if Config.gpu_boost_enabled and self.state.gpu_boosted:
                self.state.gpu_last_active = time.time()

            # 2. Preprocess audio into samples.
            samples = process_audio_task(task, result)

            # Skip inference for empty or very short audio.
            if samples is None:
                result.time_start, result.time_submit = task.time_start, task.time_submit
                result.time_complete = time.time()
                result.is_final = task.is_final
                return result

            # 3. Run recognition.
            stream = self.recognizer.create_stream()
            stream.accept_waveform(task.samplerate, samples)
            self.recognizer.decode_stream(stream, context=task.context, language=task.language)

            # Update timing metadata.
            result.time_start, result.time_submit = task.time_start, task.time_submit
            result.time_complete = time.time()

            # 4. Path A: merge text for primary output.
            asr_raw_text = stream.result.text
            logger.info(Notice('diagnostic.pipeline.recognition_decoded_task_chars'), task.task_id[:8], len(asr_raw_text))
            log_content(logger, 'asr.decoded_text', task_id=task.task_id,
                        socket_id=task.socket_id, asr_text=asr_raw_text)
            self._process_simple_merge(result, asr_raw_text)

            # 5. Path B: optional alignment for file tasks only.
            # Use the external aligner only for file tasks whose engine lacks timestamps.
            caps = self.recognizer.capabilities
            if (task.type == 'file'
                and EngineCapabilities.TIMESTAMPS not in caps 
                and self.aligner 
                and stream.result.text.strip()):
                
                logger.debug(Notice('diagnostic.pipeline.pipeline_aligning_file_segment_timestamps'))
                align_res = self.aligner.align(
                    audio=samples,
                    text=stream.result.text,
                    language=task.language,
                    offset_sec=0.0,
                    task_id=task.task_id,
                )
                if align_res and align_res.items:
                    stream.result.tokens = [it.text for it in align_res.items]
                    stream.result.timestamps = [it.start_time for it in align_res.items]


            # 6. Merge timed tokens, including native timestamps when no aligner is needed.
            new_tokens = process_tokens_safely(stream.result.tokens)
            new_timestamps = list(stream.result.timestamps)
            
            result.tokens, result.timestamps = merge_tokens_by_sequence_matcher(
                prev_tokens=result.tokens,
                prev_timestamps=result.timestamps,
                new_tokens=new_tokens,
                new_timestamps=new_timestamps,
                offset=task.offset,
                overlap=task.overlap,
                is_first_segment=is_first_segment
            )
            
            # 7. Build text_accu.
            result.text_accu = tokens_to_text(result.tokens)

            # 8. Format completed tasks.
            if not task.is_final:
                return result

            # Final task cleanup and formatting.
            raw_text = result.text
            result.text = self.formatter.format(result.text, formatting=task.formatting)
            result.text_accu = self.formatter.format(result.text_accu, formatting=task.formatting)
            logger.debug(Notice('diagnostic.pipeline.recognition_formatted_task_input_chars_output_chars'),
                         task.task_id[:8], len(raw_text), len(result.text))

            # Synchronize inserted punctuation back into the token sequence.
            if result.tokens and result.text_accu:
                result.tokens, result.timestamps = sync_tokens_from_text(
                    result.tokens, result.timestamps, result.text_accu
                )
            
            # Fall back to text when microphone mode skipped alignment and has no tokens.
            if not result.tokens and result.text:
                result.text_accu = result.text
                chars = list(result.text_accu.replace(' ', ''))
                if chars and result.duration > 0:
                    t_per_char = result.duration / len(chars)
                    result.tokens, result.timestamps = chars, [i * t_per_char for i in range(len(chars))]
            
            log_content(logger, 'asr.final_text', task_id=task.task_id,
                        socket_id=task.socket_id, asr_text=raw_text, final_text=result.text)
            diagnostic_event(logger, 'asr.task_finished', task_id=task.task_id,
                             socket_id=task.socket_id, chars=len(result.text))
            result.is_final = True
            
            # Display statistics.
            process_time = result.time_complete - task.time_submit
            rtf = process_time / result.duration if result.duration > 0 else 0
            logger.info(Notice('diagnostic.pipeline.task_completed_duration_s_elapsed_s_rtf', value0=task.task_id[:8], value1=result.duration, value2=process_time, value3=rtf))

            return result

        except Exception as e:
            logger.error(Notice('diagnostic.pipeline.recognition_pipeline_failed'), type(e).__name__)
            raise
