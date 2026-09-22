# coding: utf-8
from __future__ import annotations

from core.i18n import Notice, tr

import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from . import logger
from config_client import BASE_DIR, ClientConfig as Config
from ..state import console
from ..transcribe.lifecycle import complete_cleanup
from ..transcribe.feedback import print_file_failure


DEFAULT_MEDIA_EXTENSIONS = frozenset({
    '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma',
    '.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v', '.ts',
})


class TranscriptionTaskLog:
    """Attach a per-run transcription log and remove it when the run ends."""

    def __init__(self, base_dir: Path, *, enabled: bool = True):
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.path = self.base_dir / 'logs' / 'client_latest.log'
        self._handler: logging.FileHandler | None = None

    def start(self, *, now: datetime | None = None) -> Path:
        """Start a unique log archived under YYYY/MM."""
        if not self.enabled or self._handler is not None:
            return self.path

        now = now or datetime.now()
        log_dir = (
            self.base_dir / 'logs' / 'transcribe'
            / now.strftime('%Y') / now.strftime('%m')
        )
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stem = f'transcribe_{now:%Y%m%d-%H%M%S}'
            sequence = 1
            while True:
                suffix = '' if sequence == 1 else f' ({sequence})'
                candidate = log_dir / f'{stem}{suffix}.log'
                try:
                    handler = logging.FileHandler(
                        candidate,
                        mode='x',
                        encoding='utf-8',
                    )
                except FileExistsError:
                    sequence += 1
                    continue
                self.path = candidate
                break
        except OSError as exc:
            logger.warning(
                Notice('diagnostic.file_runner.cannot_create_file_transcription_log_using_client_log', value0=exc)
            )
            return self.path

        handler.setFormatter(logging.Formatter(
            fmt=(
                '%(asctime)s.%(msecs)03d %(levelname)-5s '
                '[%(filename)20s:%(lineno)-3d] %(message)s'
            ),
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        logger.addHandler(handler)
        self._handler = handler
        logger.info(Notice('diagnostic.file_runner.file_transcription_log_created', value0=self.path))
        return self.path

    def close(self) -> None:
        """Stop per-run logging idempotently."""
        if self._handler is None:
            return
        logger.info(Notice('diagnostic.file_runner.file_transcription_log_closed'))
        logger.removeHandler(self._handler)
        self._handler.close()
        self._handler = None


def _configured_media_extensions() -> frozenset[str]:
    """Normalize media extensions used for directory scanning."""
    configured = getattr(Config, 'file_media_extensions', DEFAULT_MEDIA_EXTENSIONS)
    try:
        extensions = {
            value if value.startswith('.') else f'.{value}'
            for item in configured
            if (value := str(item).strip().lower())
        }
    except TypeError:
        extensions = set(DEFAULT_MEDIA_EXTENSIONS)
    return frozenset(extensions or DEFAULT_MEDIA_EXTENSIONS)


def resolve_input_paths(
    inputs: list[Path],
    *,
    recursive: bool | None = None,
) -> list[Path]:
    """
    Expand files and directories into a stable, ordered, deduplicated file list.

    Keep direct media paths compatible; scan directories only for allowed extensions
    so generated TXT, JSON, and SRT files are not treated as inputs.
    """
    if recursive is None:
        recursive = bool(getattr(Config, 'file_scan_recursive', True))
    media_extensions = _configured_media_extensions()
    files = []
    seen = set()

    for input_path in inputs:
        path = input_path.expanduser()
        if path.is_dir():
            iterator = path.rglob('*') if recursive else path.iterdir()
            matches = sorted(
                (
                    candidate for candidate in iterator
                    if candidate.is_file()
                    and candidate.suffix.lower() in media_extensions
                ),
                key=lambda candidate: str(candidate).casefold(),
            )
            logger.info(Notice('diagnostic.file_runner.media_directory_scanned_recursive_files'), recursive, len(matches))
        elif path.is_file():
            matches = [path]
        else:
            console.print(
                tr('file.skip_path', value0=path)
            )
            logger.warning(Notice('diagnostic.file_runner.input_path_skipped_unavailable'))
            continue

        for candidate in matches:
            try:
                identity = str(candidate.resolve()).casefold()
            except OSError:
                identity = str(candidate.absolute()).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            files.append(candidate)

    return files


class FileRunner:
    """
    Run ASR transcription for one or more media files.
    """
    def __init__(
        self,
        app,
        files: list[Path],
        *,
        output_formats: frozenset[str],
    ):
        self.app = app
        self.files = files
        self.output_formats = output_formats
        self._failure_code = None

    @property
    def state(self):
        return self.app.state

    @property
    def ws_manager(self):
        return self.app.ws

    async def _process_file(self, file: Path):
        """Process one input; the caller reports failure and continues to the next file."""
        from ..transcribe import FileTranscriber

        transcriber = FileTranscriber(
            self.app,
            file,
            output_formats=self.output_formats,
        )
        self._failure_code = None
        tasks = []
        try:
            if not await transcriber.check():
                return None
            send_task = asyncio.create_task(transcriber.send())
            receive_task = asyncio.create_task(transcriber.receive())
            tasks = [send_task, receive_task]
            done, pending = await asyncio.wait(
                {send_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            failed = False
            for task in done:
                if task.cancelled() or task.exception() is not None:
                    failed = True
                elif task.result() is False:
                    failed = True
            if failed:
                for task in pending:
                    task.cancel()

            results = await asyncio.gather(
                send_task, receive_task, return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    logger.error(Notice('diagnostic.file_runner.file_child_task_failed_error'), type(result).__name__,
                                 extra={'console_handled': True})
            if all(result is True for result in results):
                return transcriber.summary
            return None
        finally:
            async def cleanup():
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await transcriber.close()

            try:
                await complete_cleanup(cleanup())
            finally:
                self._failure_code = getattr(transcriber, 'failure_code', None)

    async def run(self):
        """Run the file transcription coroutine."""
        from ..ui import TipsDisplay
        from ..transcribe.file_transcriber import format_duration

        base_dir = Path(getattr(self.app, 'base_dir', BASE_DIR))
        task_log = TranscriptionTaskLog(
            base_dir,
            enabled=bool(getattr(Config, 'file_separate_log', True)),
        )
        total = len(self.files)
        formats = ' / '.join(name.upper() for name in sorted(self.output_formats))
        TipsDisplay.show_file_tips(total, formats)


        succeeded_count = 0
        failed_count = 0
        summaries = []
        batch_started_at = time.perf_counter()
        log_path = task_log.start()
        logger.info(Notice('diagnostic.file_runner.file_batch_started_files_formats'), total, sorted(self.output_formats))
        try:
            for index, file in enumerate(self.files, start=1):
                if hasattr(self.app, 'config_reload'):
                    self.app._file_active = False
                    self.app.apply_config_reload()
                    self.app._file_active = True

                console.print()
                console.print(
                    f'[ui.secondary]{index:02d}[/]  [ui.title]{file.name}[/]  '
                    f'[ui.muted]{index}/{total}[/]'
                )
                console.print(tr('file.source', value0=file))
                logger.info(Notice('diagnostic.file_runner.file_processing_started_index_total'), index, total)
                try:
                    summary = await self._process_file(file)
                except Exception as exc:
                    summary = None
                    if self._failure_code is None:
                        self._failure_code = 'timeout' if isinstance(exc, TimeoutError) else 'unexpected'
                    logger.error(
                        Notice('diagnostic.file_runner.file_processing_failed_error'), type(exc).__name__,
                        extra={'console_handled': True},
                    )

                if summary is not None:
                    succeeded_count += 1
                    summaries.append(summary)
                    speed_style = 'ui.success' if summary.speed_ratio >= 1 else 'ui.warning'
                    console.print(tr('file.done', value0=file.name))
                    console.print(
                        tr(
                            'file.metrics',
                            value0=format_duration(summary.audio_duration),
                            value1=format_duration(summary.elapsed),
                            value2=speed_style,
                            value3=summary.speed_ratio,
                            value4=summary.rtf,
                            value5=summary.text_length,
                        )
                    )
                    console.print(tr('file.outputs'))
                    for output_path in summary.output_paths:
                        console.print(f'      [ui.accent]•[/] [ui.value]{output_path}[/]')
                    logger.info(Notice('diagnostic.file_runner.file_processing_completed_index_total'), index, total)
                else:
                    failed_count += 1
                    print_file_failure(console, file, self._failure_code, has_next=index < total)
                    logger.error(Notice('diagnostic.file_runner.file_task_failed_code'), self._failure_code or 'unexpected',
                                 extra={'console_handled': True})

            batch_elapsed = time.perf_counter() - batch_started_at
            total_audio = sum(summary.audio_duration for summary in summaries)
            total_outputs = sum(len(summary.output_paths) for summary in summaries)
            speed_ratio = total_audio / batch_elapsed if batch_elapsed > 0 else 0.0

            summary_table = Table.grid(padding=(0, 3))
            summary_table.add_column(style='ui.label', no_wrap=True)
            summary_table.add_column(style='ui.value')
            summary_table.add_row(
                tr('file.files'),
                tr('file.summary_counts', value0=succeeded_count, value1=failed_count, value2=total_outputs),
            )
            summary_table.add_row(
                tr('file.performance'),
                tr(
                    'file.summary_metrics',
                    value0=format_duration(total_audio),
                    value1=format_duration(batch_elapsed),
                    value2=speed_ratio,
                ),
            )
            summary_table.add_row(tr('file.log'), str(log_path))
            console.print()
            console.print(Panel(
                summary_table,
                title=tr('file.summary'),
                title_align='left',
                border_style='ui.border',
                padding=(0, 2),
            ))
            logger.info(
                Notice('diagnostic.file_runner.file_batch_completed_total_succeeded_failed_audio_s', value0=total, value1=succeeded_count, value2=failed_count, value3=total_audio, value4=batch_elapsed, value5=speed_ratio, value6=total_outputs)
            )
            
            # Keep packaged double-click and drag-and-drop windows open for result review.
            # Missing stdin in conda run or redirected sessions must not turn success into failure.
            if sys.stdin is not None and sys.stdin.isatty():
                try:
                    input(tr('file.exit_prompt'))
                except EOFError:
                    logger.debug(Notice('diagnostic.file_runner.standard_input_closed_exiting_file_mode'))

            return failed_count == 0 and succeeded_count == total

        except Exception as e:
            logger.error(Notice('diagnostic.file_runner.file_runner_failed_error'), type(e).__name__)
            return False
        finally:
            task_log.close()
