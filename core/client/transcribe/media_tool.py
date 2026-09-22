
from core.i18n import Notice, tr

# coding: utf-8
import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from core.client.state import console
from . import logger
from .lifecycle import (
    PROBE_TIMEOUT_SECONDS, complete_cleanup, open_process, reap_process,
)

class MediaTool:
    """媒体工具类：负责 FFmpeg 相关操作"""

    @staticmethod
    def check_environment() -> bool:
        """检查 FFmpeg 和 ffprobe 环境"""
        ffmpeg_path = shutil.which('ffmpeg')
        ffprobe_path = shutil.which('ffprobe')
        
        if ffmpeg_path is None:
            console.print(tr('file.ffmpeg_missing'))
            console.print(tr('file.ffmpeg_required'))
            console.print(tr('file.ffmpeg_fix'))
            console.print(tr('file.ffmpeg_download'))
            logger.error(Notice('diagnostic.media_tool.ffmpeg_unavailable'), extra={'console_handled': True})
            return False
            
        if ffprobe_path is None:
            console.print(tr('file.ffprobe_missing'))
            console.print(tr('file.ffprobe_progress'))
            console.print(tr('file.ffprobe_fix'))
            logger.warning(Notice('diagnostic.media_tool.ffprobe_unavailable'), extra={'console_handled': True})
            
        return True

    @staticmethod
    async def get_audio_duration(file: Path) -> float:
        """获取音视频文件时长"""
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(file)
        ]
        process = None
        try:
            process = await open_process(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), PROBE_TIMEOUT_SECONDS)
            if process.returncode == 0:
                return float(stdout.decode().strip())
        except Exception as exc:
            logger.warning(Notice('diagnostic.media_tool.media_duration_unavailable'), type(exc).__name__,
                           extra={'console_handled': True})
        finally:
            if process is not None:
                await complete_cleanup(reap_process(process))
        return 0.0

    @staticmethod
    def build_ffmpeg_cmd(file: Path) -> List[str]:
        """构建提取音频的 FFmpeg 命令"""
        return [
            "ffmpeg", "-i", str(file),
            "-f", "f32le", "-ac", "1", "-ar", "16000", "-"
        ]
