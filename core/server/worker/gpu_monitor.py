# coding: utf-8
"""轻量 GPU 显存压力监控。

仅在识别任务执行期间按低频率调用 ``nvidia-smi``。监控结果用于给出
诊断提示，不参与任务调度，也不会把“显存占用高”表述成已经证实的换页。
"""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
from typing import Iterable, Optional

from rich.panel import Panel

from . import logger


@dataclass(frozen=True)
class GpuSample:
    """一次 NVIDIA GPU 采样。"""

    used_mib: float
    total_mib: float
    utilization: float

    @property
    def memory_ratio(self) -> float:
        return self.used_mib / self.total_mib if self.total_mib > 0 else 0.0


def parse_nvidia_smi_output(output: str) -> list[GpuSample]:
    """解析 ``nvidia-smi --format=csv,noheader,nounits`` 输出。"""
    samples = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(',')]
        if len(fields) != 3:
            continue
        try:
            sample = GpuSample(*(float(field) for field in fields))
        except ValueError:
            continue
        if sample.total_mib > 0:
            samples.append(sample)
    return samples


class GpuPressureDetector:
    """仅在显存压力连续出现时触发一次，避免瞬时峰值误报。"""

    def __init__(self, threshold: float = 0.90, consecutive_samples: int = 3):
        self.threshold = min(1.0, max(0.5, float(threshold)))
        self.consecutive_samples = max(1, int(consecutive_samples))
        self._high_count = 0
        self._reported = False

    def observe(self, sample: GpuSample) -> bool:
        if sample.memory_ratio >= self.threshold:
            self._high_count += 1
        else:
            self._high_count = 0
            # 留出 5% 回差；压力真正解除后，同一长任务可再次报告。
            if sample.memory_ratio < self.threshold - 0.05:
                self._reported = False

        if self._high_count < self.consecutive_samples or self._reported:
            return False
        self._reported = True
        return True

    def reset(self) -> None:
        self._high_count = 0
        self._reported = False


class GpuMemoryMonitor:
    """在后台采样 NVIDIA 专用显存，并向服务端终端发出压力告警。"""

    def __init__(
        self,
        console,
        *,
        enabled: bool = True,
        interval: float = 1.0,
        threshold: float = 0.90,
        consecutive_samples: int = 3,
    ):
        self.console = console
        self.enabled = bool(enabled)
        try:
            self.interval = max(0.5, float(interval))
        except (TypeError, ValueError):
            self.interval = 1.0
        try:
            self.detector = GpuPressureDetector(threshold, consecutive_samples)
        except (TypeError, ValueError):
            self.detector = GpuPressureDetector()
        self._active = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._unavailable = False

    def begin_task(self) -> None:
        if not self.enabled or self._unavailable:
            return
        self._active.set()
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run,
                name='gpu-memory-monitor',
                daemon=True,
            )
            self._thread.start()

    def end_task(self) -> None:
        self._active.clear()

    def close(self) -> None:
        self._stop.set()
        self._active.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        was_active = False
        while not self._stop.is_set():
            if not self._active.wait(timeout=0.5):
                if was_active:
                    self.detector.reset()
                    was_active = False
                continue
            if self._stop.is_set():
                break
            was_active = True

            samples = self._query_samples()
            sample = highest_pressure(samples)
            if sample and self.detector.observe(sample):
                self._warn(sample)
            if self._stop.wait(self.interval):
                break

    def _query_samples(self) -> list[GpuSample]:
        command = [
            'nvidia-smi',
            '--query-gpu=memory.used,memory.total,utilization.gpu',
            '--format=csv,noheader,nounits',
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=2,
                check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            self._disable(exc)
            return []

        if completed.returncode != 0:
            self._disable(completed.stderr.strip() or f'exit={completed.returncode}')
            return []
        return parse_nvidia_smi_output(completed.stdout)

    def _disable(self, reason) -> None:
        self._unavailable = True
        self._active.clear()
        logger.info(f'NVIDIA 显存监控不可用，已静默停用: {reason}')

    def _warn(self, sample: GpuSample) -> None:
        used_gib = sample.used_mib / 1024
        total_gib = sample.total_mib / 1024
        percent = sample.memory_ratio * 100
        logger.warning(
            f'GPU 专用显存持续高压: {used_gib:.1f}/{total_gib:.1f} GiB '
            f'({percent:.0f}%), GPU 利用率 {sample.utilization:.0f}%'
        )
        self.console.print(Panel.fit(
            f'[bold red]专用显存持续占用 {used_gib:.1f}/{total_gib:.1f} GiB '
            f'({percent:.0f}%)[/bold red]\n'
            f'[yellow]GPU 利用率 {sample.utilization:.0f}%；Windows 可能开始把 GPU '
            '资源迁移到共享内存，转写速度可能明显波动。[/yellow]\n'
            '[dim]此告警表示“显存压力高”，不能单独证明已经发生显存交换。'
            '可同时观察任务管理器的“共享 GPU 内存”和转写速度。[/dim]',
            title='[bold red]GPU 显存压力告警[/bold red]',
            border_style='bold red',
        ))


def highest_pressure(samples: Iterable[GpuSample]) -> Optional[GpuSample]:
    """供诊断代码复用；空输入返回 None。"""
    return max(samples, key=lambda item: item.memory_ratio, default=None)
