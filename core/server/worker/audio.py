# coding: utf-8
"""
Audio preprocessing.

Convert raw audio bytes to numpy.float32 samples
and accumulate processed duration.
"""

import numpy as np
from typing import Optional
from core.server.schema import Task, Result


def process_audio_task(task: Task, result: Result) -> Optional[np.ndarray]:
    """
    Process one audio segment and update result duration.
    
    Args:
        task: Recognition task.
        result: Recognition result.
        
    Returns:
        samples: float32 NumPy array, or None for empty/short audio.
    """
    # 1. Convert bytes to float32 samples.
    samples = np.frombuffer(task.data, dtype=np.float32)

    # Skip audio shorter than 1600 samples (about 0.1 seconds at 16 kHz).
    if len(samples) < 1600:
        return None

    # 2. Compute this segment's duration contribution.
    # Subtract overlap except for the final segment, where it contributes to total duration.
    duration = len(samples) / task.samplerate
    
    # Update cumulative duration.
    result.duration += duration - task.overlap
    if task.is_final:
        result.duration += task.overlap
        
    return samples
