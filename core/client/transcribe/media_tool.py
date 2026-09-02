# coding: utf-8
import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from core.client.state import console
from . import logger

class MediaTool:
    """媒体工具类：负责 FFmpeg 相关操作"""

    @staticmethod
    def check_environment() -> bool:
        """检查 FFmpeg 和 ffprobe 环境"""
        ffmpeg_path = shutil.which('ffmpeg')
        ffprobe_path = shutil.which('ffprobe')
        
        if ffmpeg_path is None:
            console.print('\n[ui.error]✗ 缺少 FFmpeg[/]')
            console.print('[ui.value]文件转写需要 FFmpeg 提取音轨。[/]')
            console.print('[ui.label]解决方法[/]  [ui.value]将 FFmpeg 的 bin 目录加入 Path，'
                          '或把 ffmpeg.exe 放到程序目录。[/]')
            console.print('[ui.label]下载[/]  [link=https://ffmpeg.org/download.html]'
                          'https://ffmpeg.org/download.html[/link]\n')
            logger.error("未检测到 FFmpeg 环境，无法进行文件转录")
            return False
            
        if ffprobe_path is None:
            console.print('\n[ui.warning]▲ 未检测到 ffprobe[/]')
            console.print('[ui.value]读取完整段音频前，仅显示已处理时长；随后补全百分比与 ETA。[/]')
            console.print('[ui.label]建议[/]  [ui.value]将 ffprobe.exe 与 ffmpeg.exe 一同安装。[/]\n')
            logger.warning("未检测到 ffprobe 环境，进度显示将受到限制")
            
        return True

    @staticmethod
    async def get_audio_duration(file: Path) -> float:
        """获取音视频文件时长"""
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(file)
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                return float(stdout.decode().strip())
        except Exception as e:
            logger.warning(f"无法通过 ffprobe 获取时长: {e}")
        return 0.0

    @staticmethod
    def build_ffmpeg_cmd(file: Path) -> List[str]:
        """构建提取音频的 FFmpeg 命令"""
        return [
            "ffmpeg", "-i", str(file),
            "-f", "f32le", "-ac", "1", "-ar", "16000", "-"
        ]
