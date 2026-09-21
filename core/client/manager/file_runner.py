# coding: utf-8
from __future__ import annotations
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
    """为一次文件转写运行附加独立日志，并在结束时安全移除。"""

    def __init__(self, base_dir: Path, *, enabled: bool = True):
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.path = self.base_dir / 'logs' / 'client_latest.log'
        self._handler: logging.FileHandler | None = None

    def start(self, *, now: datetime | None = None) -> Path:
        """开始记录；独立日志按 ``年份/月份`` 归档且不会覆盖。"""
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
                f'无法创建文件转写独立日志，将继续使用客户端日志: {exc}'
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
        logger.info(f'文件转写独立日志已创建: {self.path}')
        return self.path

    def close(self) -> None:
        """停止独立日志记录；可重复调用。"""
        if self._handler is None:
            return
        logger.info('文件转写独立日志记录结束')
        logger.removeHandler(self._handler)
        self._handler.close()
        self._handler = None


def _configured_media_extensions() -> frozenset[str]:
    """读取并规范化目录扫描使用的媒体扩展名。"""
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
    将文件和文件夹参数展开成稳定、有序且去重的文件列表。

    直接传入的媒体文件保持兼容；文件夹只扫描配置允许的媒体格式，避免把
    已生成的 txt/json/srt 再次当作输入。
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
            mode = '递归扫描' if recursive else '扫描'
            logger.info(f'{mode}文件夹: {path}, 媒体文件数: {len(matches)}')
        elif path.is_file():
            matches = [path]
        else:
            console.print(
                f'[ui.warning]▲ 跳过不存在的路径[/]  [ui.value]{path}[/]'
            )
            logger.warning(f'跳过不存在的输入路径: {path}')
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
    文件模式运行器：负责一个或多个音视频文件的 ASR 转录。
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
        """处理单个输入；失败由调用方记录后继续下一个文件。"""
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
                    logger.error('File child task failed: error=%s', type(result).__name__,
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
        """文件转录模式主循环 (Coroutine)"""
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
        logger.info(
            f"文件转写任务开始: 文件数={total}, 输出格式={sorted(self.output_formats)}, "
            f"待处理文件={[str(f) for f in self.files]}"
        )
        try:
            for index, file in enumerate(self.files, start=1):

                console.print()
                console.print(
                    f'[ui.secondary]{index:02d}[/]  [ui.title]{file.name}[/]  '
                    f'[ui.muted]{index}/{total}[/]'
                )
                console.print(f'    [ui.label]来源[/]  [ui.value]{file}[/]')
                logger.info(f"正在处理文件: {file}")
                try:
                    summary = await self._process_file(file)
                except Exception as exc:
                    summary = None
                    if self._failure_code is None:
                        self._failure_code = 'timeout' if isinstance(exc, TimeoutError) else 'unexpected'
                    logger.error(
                        'File processing failed: error=%s', type(exc).__name__,
                        exc_info=True,
                        extra={'console_handled': True},
                    )

                if summary is not None:
                    succeeded_count += 1
                    summaries.append(summary)
                    speed_style = 'ui.success' if summary.speed_ratio >= 1 else 'ui.warning'
                    console.print(f'[ui.success]✓ 完成[/]  [ui.value]{file.name}[/]')
                    console.print(
                        f'    [ui.label]音频[/] [ui.value]{format_duration(summary.audio_duration)}[/]    '
                        f'[ui.label]耗时[/] [ui.value]{format_duration(summary.elapsed)}[/]    '
                        f'[ui.label]速度[/] [{speed_style}]{summary.speed_ratio:.2f}×[/]    '
                        f'[ui.label]RTF[/] [ui.value]{summary.rtf:.3f}[/]    '
                        f'[ui.label]文本[/] [ui.value]{summary.text_length} 字[/]'
                    )
                    console.print('    [ui.label]输出[/]')
                    for output_path in summary.output_paths:
                        console.print(f'      [ui.accent]•[/] [ui.value]{output_path}[/]')
                    logger.info(f"文件处理完成: {file}")
                else:
                    failed_count += 1
                    print_file_failure(console, file, self._failure_code, has_next=index < total)
                    logger.error('File task failed: code=%s', self._failure_code or 'unexpected',
                                 extra={'console_handled': True})

            batch_elapsed = time.perf_counter() - batch_started_at
            total_audio = sum(summary.audio_duration for summary in summaries)
            total_outputs = sum(len(summary.output_paths) for summary in summaries)
            speed_ratio = total_audio / batch_elapsed if batch_elapsed > 0 else 0.0

            summary_table = Table.grid(padding=(0, 3))
            summary_table.add_column(style='ui.label', no_wrap=True)
            summary_table.add_column(style='ui.value')
            summary_table.add_row(
                '文件',
                f'[ui.success]{succeeded_count} 成功[/]  '
                f'[ui.error]{failed_count} 失败[/]  ·  {total_outputs} 个输出',
            )
            summary_table.add_row(
                '性能',
                f'音频 {format_duration(total_audio)}  ·  '
                f'耗时 {format_duration(batch_elapsed)}  ·  '
                f'[ui.accent]{speed_ratio:.2f}× 实时[/]',
            )
            summary_table.add_row('日志', str(log_path))
            console.print()
            console.print(Panel(
                summary_table,
                title='[ui.accent]转写汇总[/]',
                title_align='left',
                border_style='ui.border',
                padding=(0, 2),
            ))
            logger.info(
                f"所有文件已处理完成: 总数={total}, "
                f"成功={succeeded_count}, 失败={failed_count}, "
                f"音频总时长={total_audio:.2f}s, 总耗时={batch_elapsed:.2f}s, "
                f"速度={speed_ratio:.2f}x, 输出文件数={total_outputs}"
            )
            
            # 打包版双击/拖拽启动时保留窗口，便于用户查看结果；
            # conda run、重定向或其他无 stdin 场景应当正常结束，不能把成功任务报成失败。
            if sys.stdin is not None and sys.stdin.isatty():
                try:
                    input('\n按回车退出\n')
                except EOFError:
                    logger.debug("标准输入已关闭，文件模式直接退出")

            return failed_count == 0 and succeeded_count == total

        except Exception as e:
            logger.error(f"文件模式运行异常: {e}", exc_info=True)
            raise
        finally:
            task_log.close()
