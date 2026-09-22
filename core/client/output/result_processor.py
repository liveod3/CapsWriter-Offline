"""最终识别结果：可选单次文本动作、焦点保护与独立归档。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from config_client import ClientConfig as Config
from core.protocol import RecognitionMessage
from core.client.state import console
from core.client.caret_context import foreground_window
from core.client.connection import CommunicationError
from core.client.dictation_lifecycle import (
    MAX_PENDING_DICTATIONS, close_dictation_connection, cancel_dictation,
)
from core.client.transcribe.lifecycle import complete_cleanup
from core.client.audio.file_manager import AudioFileManager
from core.client.output.text_output import TextOutput
from core.client.udp.udp_broadcaster import broadcast_output_udp
from core.tools.window_detector import get_active_window_info
from . import logger


class ResultProcessor:
    def __init__(self, app):
        self.app = app
        self._loop = asyncio.get_running_loop()
        self._exit_event = asyncio.Event()
        self._ready_results = asyncio.Queue(maxsize=MAX_PENDING_DICTATIONS)
        self._received = set()
        self._deadline_poll = 0.25
        self._cancellations = set()

    @property
    def state(self):
        return self.app.state

    @property
    def ws(self):
        return self.app.ws

    def request_exit(self):
        self.app.progress.close()
        if self._loop.is_running() and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._exit_event.set)

    async def start(self):
        """Receive and expire tasks independently of serial LLM/output work."""
        children = [asyncio.create_task(operation) for operation in (
            self._receive_loop(), self._process_results(), self._watch_deadlines(),
            self._exit_event.wait(),
        )]
        try:
            done, _ = await asyncio.wait(children, return_when=asyncio.FIRST_COMPLETED)
            for child in done:
                child.result()
        finally:
            self._exit_event.set()
            async def cleanup():
                for child in children:
                    child.cancel()
                await asyncio.gather(*children, return_exceptions=True)
                for operation in self._cancellations:
                    operation.cancel()
                await asyncio.gather(*self._cancellations, return_exceptions=True)
                self._received.clear()
                self._fail_unfinished(notify=False)
                while not self._ready_results.empty():
                    self._ready_results.get_nowait()
            await complete_cleanup(cleanup())

    async def _receive_loop(self):
        while not self._exit_event.is_set():
            if not await self.ws.connect():
                await asyncio.sleep(2)
                continue
            while not self._exit_event.is_set():
                websocket = self.state.websocket
                try:
                    message = await self.ws.receive()
                except CommunicationError as exc:
                    # 接收异常属于连接边界，不能让它结束整个麦克风运行器。
                    # 主动退出保持安静；其他断线清理后由外层循环重新连接。
                    if not self._exit_event.is_set():
                        logger.warning("Connection interrupted: %s", type(exc).__name__)
                    self._fail_unfinished(notify=not self._exit_event.is_set())
                    await close_dictation_connection(self.state, websocket)
                    break
                if message is None:
                    self._fail_unfinished(notify=not self._exit_event.is_set())
                    break
                try:
                    await self._handle_message(message, enqueue=True)
                except Exception as exc:
                    logger.error("Result processing failed: %s", type(exc).__name__)
            if not self._exit_event.is_set():
                console.print("[ui.warning]Connection lost. Reconnecting…[/]")

    async def _handle_message(self, message: RecognitionMessage | None, *, enqueue=False):
        if message is None or message.is_final is not True:
            return
        if getattr(message, 'error_code', ''):
            self._handle_error(message)
            return
        task_id = message.task_id
        # Only a final submission can own a successful terminal response.
        # Claim it before yielding so duplicates and timed-out replies are inert.
        if task_id not in self.state.dictation_deadlines:
            return
        if time.monotonic() >= self.state.dictation_deadlines[task_id]:
            self._fail_task(task_id, 'result_timeout', '识别等待超时，请重试本次听写。')
            return
        self.state.dictation_deadlines.pop(task_id)
        self._received.add(task_id)
        if enqueue:
            self._ready_results.put_nowait(message)
        else:
            await self._process_final(message)

    async def _process_results(self):
        while True:
            message = await self._ready_results.get()
            try:
                await self._process_final(message)
            except Exception as exc:
                logger.error('Result processing failed: %s', type(exc).__name__)

    async def _process_final(self, message):
        task_id = message.task_id
        upload = self.state.dictation_uploads.get(task_id)
        try:
            if upload is None:
                return
            await upload.wait()
            if (self.state.dictation_uploads.get(task_id) is upload
                    and task_id in self.state.task_contexts and not self._exit_event.is_set()):
                await self._handle_final(message)
        finally:
            self._received.discard(task_id)
            self.state.dictation_uploads.pop(task_id, None)
            self.state.task_contexts.pop(task_id, None)
            self.state.pop_audio_file(task_id)
            self.app.progress.finish(task_id)

    async def _watch_deadlines(self):
        while True:
            await asyncio.sleep(self._deadline_poll)
            self._expire_tasks()

    def _expire_tasks(self):
        now = time.monotonic()
        for task_id, deadline in list(self.state.dictation_deadlines.items()):
            if now >= deadline:
                self._fail_task(task_id, 'result_timeout', '识别等待超时，请重试本次听写。')

    def _fail_unfinished(self, *, notify):
        tasks = set(self.state.dictation_uploads) | set(self.state.task_contexts)
        with self.state.recording_lock:
            tasks.update(self.state.recorder_by_id)
        for task_id in tasks - self._received:
            self._fail_task(task_id, 'connection_lost', '连接已断开，请重试本次听写。', notify=notify)

    def _handle_error(self, message: RecognitionMessage):
        """Reject unknown/duplicate errors and cancel only their recording owner."""
        if message.task_id not in self._received:
            self._fail_task(message.task_id, message.error_code, '识别失败，请重试本次听写。')

    def _fail_task(self, task_id, code, feedback, *, notify=True):
        with self.state.recording_lock:
            owner = self.state.recording_owner
            owns_capture = owner is not None and owner._progress_id == task_id
            future = self.state.recorder_by_id.pop(task_id, None)
            self.state.dictation_deadlines.pop(task_id, None)
            if (not owns_capture and future is None and task_id not in self.state.task_contexts
                    and task_id not in self.state.audio_files
                    and task_id not in self.state.dictation_uploads):
                return
            upload = self.state.dictation_uploads.pop(task_id, None)
            if upload is not None:
                upload.set()
            self.state.task_contexts.pop(task_id, None)
            self.state.pop_audio_file(task_id)
            if owns_capture:
                owner.cancel()
            elif future is not None:
                future.cancel()
        self.app.progress.finish(task_id)
        if code == 'result_timeout' and self.state.websocket is not None:
            if len(self._cancellations) >= MAX_PENDING_DICTATIONS:
                self.state.websocket.transport.abort()
            else:
                operation = asyncio.create_task(cancel_dictation(self.state, self.state.websocket, task_id))
                self._cancellations.add(operation)
                operation.add_done_callback(self._cancellations.discard)
        if not notify:
            return
        logger.warning('Dictation task failed: task=%s code=%s', task_id[:8], code,
                       extra={'console_handled': True})
        console.print(f'[ui.error]{feedback}[/]')
        # Recheck under the ownership lock: a shortcut may have started a new
        # recording while diagnostics were emitted above.
        with self.state.recording_lock:
            if self.state.recording_owner is None and not getattr(self.app, '_stopping', False):
                from core.ui import show_status_hint
                show_status_hint(feedback, duration_ms=3500, dot_color='#EF4444')

    async def _handle_final(self, message: RecognitionMessage):
        self.app.progress.update(message.task_id, "Preparing text…")
        original = message.text
        text = original
        if Config.traditional_convert:
            from core.tools.zhconv import convert

            text = convert(text, Config.traditional_locale)
        text = TextOutput.strip_punc(text)
        self.state.last_recognition_text = original
        context, target_window = self.state.task_contexts.pop(message.task_id, ("", 0))
        logger.info("Final transcription: task=%s chars=%d", message.task_id[:8], len(original))
        result = await self.app.llm.process(
            text, context=context,
            progress_callback=lambda stage: self.app.progress.update(message.task_id, stage),
        )
        self.app.progress.finish(message.task_id)
        final_text = result.text
        # 无论失败/取消都可从菜单找回本次文字；取消不会自动上屏。
        self.state.set_output_text(final_text)
        console.print("Transcription:", original, markup=False)
        if result.processed:
            console.print("LLM action:", final_text, markup=False)

        from core.ui import show_status_hint

        if result.error:
            show_status_hint(
                (result.error_message or "LLM action failed.")
                + " Original transcription retained.",
                duration_ms=5000,
            )
        can_output = not result.cancelled and not self._exit_event.is_set()
        if target_window and foreground_window() != target_window:
            can_output = False
            show_status_hint("Result ready. Use Copy last result.", duration_ms=3000)
        if can_output:
            info = get_active_window_info()
            process_name = info.get("process_name", "").lower()
            paste = Config.paste or any(name.lower() == process_name for name in Config.paste_apps)
            # LLM 等完整结果返回后一次性输出。
            await self.app.output.output(final_text, paste=paste)
            broadcast_output_udp(final_text)
            for application, delay in Config.enter_apps:
                if application.lower() == process_name:
                    await asyncio.sleep(delay)
                    if not self._exit_event.is_set() and (
                        not target_window or foreground_window() == target_window
                    ):
                        import keyboard

                        keyboard.press_and_release("enter")
                    break
        await asyncio.to_thread(self._save, message, original, final_text, result)

    def _save(self, message, original: str, final_text: str, result):
        audio_path = self.state.pop_audio_file(message.task_id)
        if audio_path and Config.save_audio:
            manager = AudioFileManager()
            manager.file_path = Path(audio_path)
            audio_path = manager.rename(original, message.time_start)
        if getattr(Config, "save_transcripts", False):
            try:
                self.app.diary.write(
                    final_text,
                    message.time_start,
                    audio_path,
                    original=original
                    if getattr(Config, "transcript_save_original", False)
                    else None,
                )
            except OSError as exc:
                logger.error("Transcript archive failed: %s", type(exc).__name__)
        if result.processed and getattr(Config, "save_llm_records", False):
            try:
                self.app.action_records.write(
                    final_text, message.time_start, audio_path, action_input=result.input_text
                )
            except OSError as exc:
                logger.error("Text action archive failed: %s", type(exc).__name__)
