# coding: utf-8
"""
通信协议模块

定义客户端与服务端之间的消息协议数据类。
这些类同时用于服务端和客户端，确保消息格式一致。
"""

from __future__ import annotations
import base64
import binascii
from dataclasses import dataclass, field, asdict
import math
from typing import List, Literal, Optional, cast
import json


MAX_TASK_ID_LENGTH = 128
MAX_CONTEXT_LENGTH = 4096
MAX_LANGUAGE_LENGTH = 32
MAX_AUDIO_MESSAGE_BYTES = 4 * 1024 * 1024
MIN_SEG_DURATION = 0.1
MAX_SEG_DURATION = 120.0
MAX_SEG_OVERLAP = 30.0


class ProtocolValidationError(ValueError):
    """收到不符合协议约束的消息。"""


def _finite_number(data: dict, field_name: str, *, default=None) -> float:
    """读取有限数值，显式拒绝 bool 和可疑的字符串转换。"""
    if field_name in data:
        value = data[field_name]
    elif default is not None:
        value = default
    else:
        raise ProtocolValidationError(f'缺少字段: {field_name}')

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolValidationError(f'{field_name} 必须是数值')
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ProtocolValidationError(f'{field_name} 必须是有限数值') from exc
    if not math.isfinite(value):
        raise ProtocolValidationError(f'{field_name} 必须是有限数值')
    return value


@dataclass
class AudioMessage:
    """
    客户端 -> 服务端：音频数据消息
    
    Attributes:
        task_id: 任务唯一标识
        source: 音频来源 ('mic' 麦克风 或 'file' 文件)
        data: Base64 编码的音频数据 (float32, 16kHz, mono)
        is_final: 是否为当前任务的最后一个数据包
        time_start: 录音/音频开始时间戳
        seg_duration: 分段时长（秒）
        seg_overlap: 重叠时长（秒）
    """
    task_id: str
    source: Literal['mic', 'file']
    data: str                    # base64 编码的音频
    is_final: bool
    time_start: float
    seg_duration: float = 15.0
    seg_overlap: float = 2.0
    context: str = ''
    language: str = 'auto'
    supports_task_errors: bool = False

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(asdict(self), ensure_ascii=False)
    
    @classmethod
    def from_dict(
        cls,
        data: dict,
        *,
        max_audio_bytes: int = MAX_AUDIO_MESSAGE_BYTES,
        max_context_length: int = MAX_CONTEXT_LENGTH,
    ) -> AudioMessage:
        """从不可信字典创建实例，并执行协议边界校验。"""
        if not isinstance(data, dict):
            raise ProtocolValidationError('消息必须是 JSON 对象')
        if (
            isinstance(max_audio_bytes, bool)
            or not isinstance(max_audio_bytes, int)
            or max_audio_bytes <= 0
        ):
            raise ValueError('max_audio_bytes 必须是正整数')
        if (
            isinstance(max_context_length, bool)
            or not isinstance(max_context_length, int)
            or max_context_length <= 0
        ):
            raise ValueError('max_context_length 必须是正整数')

        task_id = data.get('task_id')
        if not isinstance(task_id, str) or not task_id or len(task_id) > MAX_TASK_ID_LENGTH:
            raise ProtocolValidationError(
                f'task_id 必须是 1-{MAX_TASK_ID_LENGTH} 个字符的字符串'
            )
        if any(ord(char) < 32 for char in task_id):
            raise ProtocolValidationError('task_id 不得包含控制字符')

        source = data.get('source')
        if not isinstance(source, str) or source not in {'mic', 'file'}:
            raise ProtocolValidationError("source 必须是 'mic' 或 'file'")

        encoded_audio = data.get('data')
        if not isinstance(encoded_audio, str):
            raise ProtocolValidationError('data 必须是 Base64 字符串')
        try:
            audio_data = base64.b64decode(encoded_audio, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProtocolValidationError('data 不是有效的 Base64') from exc
        if len(audio_data) > max_audio_bytes:
            raise ProtocolValidationError(
                f'单条消息音频超过 {max_audio_bytes} 字节上限'
            )
        if len(audio_data) % 4 != 0:
            raise ProtocolValidationError('音频字节数必须按 float32（4 字节）对齐')

        is_final = data.get('is_final')
        if not isinstance(is_final, bool):
            raise ProtocolValidationError('is_final 必须是布尔值')
        if not is_final and not audio_data:
            raise ProtocolValidationError('非最终消息不得包含空音频')

        time_start = _finite_number(data, 'time_start')
        if time_start < 0:
            raise ProtocolValidationError('time_start 不得为负数')
        seg_duration = _finite_number(data, 'seg_duration', default=15.0)
        seg_overlap = _finite_number(data, 'seg_overlap', default=2.0)
        if not MIN_SEG_DURATION <= seg_duration <= MAX_SEG_DURATION:
            raise ProtocolValidationError(
                f'seg_duration 必须在 {MIN_SEG_DURATION}-{MAX_SEG_DURATION} 秒之间'
            )
        if not 0 <= seg_overlap <= MAX_SEG_OVERLAP:
            raise ProtocolValidationError(
                f'seg_overlap 必须在 0-{MAX_SEG_OVERLAP} 秒之间'
            )
        if seg_overlap >= seg_duration:
            raise ProtocolValidationError('seg_overlap 必须小于 seg_duration')

        context = data.get('context', '')
        if not isinstance(context, str) or len(context) > max_context_length:
            raise ProtocolValidationError(
                f'context 必须是至多 {max_context_length} 个字符的字符串'
            )

        language = data.get('language', 'auto')
        if (
            not isinstance(language, str)
            or not language
            or len(language) > MAX_LANGUAGE_LENGTH
        ):
            raise ProtocolValidationError(
                f'language 必须是 1-{MAX_LANGUAGE_LENGTH} 个字符的字符串'
            )

        supports_task_errors = data.get('supports_task_errors', False)
        if not isinstance(supports_task_errors, bool):
            raise ProtocolValidationError('supports_task_errors must be a boolean')

        message = cls(
            task_id=task_id,
            source=cast(Literal['mic', 'file'], source),
            data=encoded_audio,
            is_final=is_final,
            time_start=time_start,
            seg_duration=seg_duration,
            seg_overlap=seg_overlap,
            context=context,
            language=language,
            supports_task_errors=supports_task_errors,
        )
        # 避免服务端在校验后再次解码；动态属性不会进入 asdict()/线协议。
        setattr(message, '_audio_bytes', audio_data)
        return message

    def decode_audio(self) -> bytes:
        """返回严格解码后的音频；from_dict() 创建的消息会复用校验结果。"""
        cached = getattr(self, '_audio_bytes', None)
        if cached is not None:
            return cached
        try:
            return base64.b64decode(self.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProtocolValidationError('data 不是有效的 Base64') from exc


@dataclass
class RecognitionMessage:
    """
    服务端 -> 客户端：识别结果消息
    
    Attributes:
        task_id: 任务唯一标识
        is_final: 是否为最终结果（所有片段识别完成）
        duration: 已处理的音频总时长（秒）
        time_start: 录音/音频开始时间戳
        time_submit: 最后一个片段的提交时间戳
        time_complete: 识别完成时间戳
        
        text: 主要输出 - 简单文本拼接结果（不依赖时间戳）
        text_accu: 精确输出 - 基于时间戳去重的拼接结果（用于字幕生成）
        tokens: 字级 token 列表（与 timestamps 对应）
        timestamps: 字级时间戳列表（秒）
    """
    task_id: str
    is_final: bool
    duration: float
    time_start: float
    time_submit: float
    time_complete: float
    
    # 主要输出（简单文本拼接）
    text: str
    
    # 精确输出（时间戳拼接）
    text_accu: str = ''
    tokens: List[str] = field(default_factory=list)
    timestamps: List[float] = field(default_factory=list)
    error_code: str = ''
    
    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(asdict(self), ensure_ascii=False)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> RecognitionMessage:
        """从字典创建实例"""
        if not isinstance(data, dict):
            raise ProtocolValidationError('Recognition message must be an object')
        error_code = data.get('error_code', '')
        if not isinstance(error_code, str) or error_code not in {'', 'recognition_failed'}:
            raise ProtocolValidationError('Unknown task error code')
        if error_code:
            task_id = data.get('task_id')
            if (not isinstance(task_id, str) or not 0 < len(task_id) <= MAX_TASK_ID_LENGTH
                    or any(ord(char) < 32 for char in task_id)):
                raise ProtocolValidationError('Invalid error task identity')
            if data.get('is_final') is not True:
                raise ProtocolValidationError('Task errors must be terminal')
            if (data.get('text') != '' or data.get('text_accu', '') != ''
                    or data.get('tokens', []) != [] or data.get('timestamps', []) != []):
                raise ProtocolValidationError('Task errors must not contain recognition content')
            for name in ('duration', 'time_start', 'time_submit', 'time_complete'):
                if _finite_number(data, name) < 0:
                    raise ProtocolValidationError('Invalid error timing')
        return cls(
            task_id=data['task_id'],
            is_final=data['is_final'],
            duration=data['duration'],
            time_start=data['time_start'],
            time_submit=data['time_submit'],
            time_complete=data['time_complete'],
            text=data['text'],
            text_accu=data.get('text_accu', ''),
            tokens=data.get('tokens', []),
            timestamps=data.get('timestamps', []),
            error_code=error_code,
        )
