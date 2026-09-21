"""最终识别结果：可选单次文本动作、焦点保护与独立归档。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from config_client import ClientConfig as Config
from core.protocol import RecognitionMessage
from core.client.state import console
from core.client.caret_context import foreground_window
from core.client.connection import CommunicationError
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
        while not self._exit_event.is_set():
            if not await self.ws.connect():
                await asyncio.sleep(2)
                continue
            while not self._exit_event.is_set():
                try:
                    message = await self.ws.receive()
                except CommunicationError as exc:
                    # 接收异常属于连接边界，不能让它结束整个麦克风运行器。
                    # 主动退出保持安静；其他断线清理后由外层循环重新连接。
                    if not self._exit_event.is_set():
                        logger.warning("Connection interrupted: %s", type(exc).__name__)
                    await self.ws.close()
                    break
                if message is None:
                    break
                try:
                    await self._handle_message(message)
                except Exception as exc:
                    logger.error("Result processing failed: %s", type(exc).__name__)
            self.state.task_contexts.clear()
            self.app.progress.clear()
            if not self._exit_event.is_set():
                console.print("[ui.warning]Connection lost. Reconnecting…[/]")

    async def _handle_message(self, message: RecognitionMessage | None):
        if message is None or not message.is_final:
            return
        try:
            if getattr(message, 'error_code', ''):
                self._handle_error(message)
            else:
                await self._handle_final(message)
        finally:
            self.app.progress.finish(message.task_id)

    def _handle_error(self, message: RecognitionMessage):
        """Reject unknown/duplicate errors and cancel only their recording owner."""
        with self.state.recording_lock:
            task_id = message.task_id
            owner = self.state.recording_owner
            owns_capture = owner is not None and owner._progress_id == task_id
            future = self.state.recorder_by_id.pop(task_id, None)
            if (not owns_capture and future is None and task_id not in self.state.task_contexts
                    and task_id not in self.state.audio_files):
                return
            self.state.task_contexts.pop(task_id, None)
            self.state.pop_audio_file(task_id)
            if owns_capture:
                owner.cancel()
            elif future is not None:
                future.cancel()
        logger.warning('Dictation task failed: task=%s code=%s', task_id[:8], message.error_code,
                       extra={'console_handled': True})
        console.print('[ui.error]识别失败，请重试本次听写。[/]')
        # Recheck under the ownership lock: a shortcut may have started a new
        # recording while diagnostics were emitted above.
        with self.state.recording_lock:
            if self.state.recording_owner is None and not getattr(self.app, '_stopping', False):
                from core.ui import show_status_hint
                show_status_hint('识别失败，请重试本次听写。', duration_ms=3500, dot_color='#EF4444')

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
