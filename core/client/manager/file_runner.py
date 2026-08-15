# coding: utf-8
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from . import logger
from config_client import ClientConfig as Config, __version__
from ..state import console


DEFAULT_MEDIA_EXTENSIONS = frozenset({
    '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma',
    '.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v', '.ts',
})


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
            console.print(
                f'[cyan]{mode}文件夹：[/]{path} '
                f'[dim]（发现 {len(matches)} 个媒体文件）[/]'
            )
            logger.info(f'{mode}文件夹: {path}, 媒体文件数: {len(matches)}')
        elif path.is_file():
            matches = [path]
        else:
            console.print(f'[bold yellow]跳过不存在的路径：[/]{path}')
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

    @property
    def state(self):
        return self.app.state

    @property
    def ws_manager(self):
        return self.app.ws

    async def _process_file(self, file: Path) -> bool:
        """处理单个输入；失败由调用方记录后继续下一个文件。"""
        from ..transcribe import FileTranscriber

        transcriber = FileTranscriber(
            self.app,
            file,
            output_formats=self.output_formats,
        )
        if not await transcriber.check():
            return False

        send_task = asyncio.create_task(transcriber.send())
        receive_task = asyncio.create_task(transcriber.receive())
        try:
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
                if isinstance(result, BaseException):
                    logger.error(f'文件任务子协程异常: {file}: {result}')
            return all(result is True for result in results)
        finally:
            await transcriber.close()

    async def run(self):
        """文件转录模式主循环 (Coroutine)"""
        from ..ui import TipsDisplay
        
        TipsDisplay.show_file_tips()
        total = len(self.files)
        console.print(f'\n[bold cyan]批量任务：共 {total} 个文件，将按顺序逐个处理[/]')
        logger.info(f"待处理文件: {[str(f) for f in self.files]}")
        
        
        # 加载热词资源
        self.app.hotword.start()

        succeeded_count = 0
        failed_count = 0
        try:
            for index, file in enumerate(self.files, start=1):

                console.rule(f'[cyan][{index}/{total}] {file.name}')
                console.print(f'    输入文件：{file}')
                logger.info(f"正在处理文件: {file}")
                try:
                    succeeded = await self._process_file(file)
                except Exception as exc:
                    succeeded = False
                    logger.error(
                        f'处理文件时发生异常，将继续下一个文件: {file}: {exc}',
                        exc_info=True,
                    )

                if succeeded:
                    succeeded_count += 1
                    console.print(f'[bold green]✓ [{index}/{total}] 处理完成：[/]{file.name}')
                    logger.info(f"文件处理完成: {file}")
                else:
                    failed_count += 1
                    console.print(f'[bold red]✗ [{index}/{total}] 处理失败：[/]{file.name}')
                    logger.error(f"文件处理失败: {file}")
            
            console.rule('[green]批量任务结束')
            console.print(
                f'[bold]合计：[/]{total}，'
                f'[green]成功：{succeeded_count}[/]，'
                f'[red]失败：{failed_count}[/]'
            )
            logger.info(
                f"所有文件已处理完成: 总数={total}, "
                f"成功={succeeded_count}, 失败={failed_count}"
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
            self.app.hotword.stop()
