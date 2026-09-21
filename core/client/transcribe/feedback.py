"""File-task feedback with stable failure IDs separate from display labels."""

from rich.text import Text


FAILURE_LABELS = {
    'decode_failed': ('无法读取有效音轨，文件可能损坏或格式不受支持。',
                      '请确认文件可以正常播放'),
    'missing_file': ('找不到输入文件。', '请检查文件路径，或重新选择文件。'),
    'decoder_unavailable': ('无法启动音频解码工具。', '请检查 FFmpeg 是否已安装并可从 PATH 找到。'),
    'connection_failed': ('识别服务连接已断开或无法连接。', '请确认服务端已启动，再重试此文件。'),
    'timeout': ('等待解码、发送或识别进度超时。', '请检查服务状态；负载较高时可适当提高文件转写超时设置。'),
    'invalid_result': ('识别服务返回了无效结果。', '请检查客户端与服务端版本，并查看诊断日志。'),
    'output_failed': ('无法保存转写结果。', '请检查输出目录权限和剩余磁盘空间，已有输出可能不完整。'),
    'unexpected': ('转写过程中发生异常。', '请查看本次运行的诊断日志后重试。'),
}


def print_file_failure(console, file, code, *, has_next):
    reason, action = FAILURE_LABELS.get(code, FAILURE_LABELS['unexpected'])
    console.print(Text.assemble(('✗ 无法转写', 'ui.error'), '  ', (file.name, 'ui.value')))
    console.print(Text.assemble(('    原因  ', 'ui.label'), (reason, 'ui.value')))
    console.print(Text.assemble(('    建议  ', 'ui.label'), (action, 'ui.value')))
    status = '已跳过此文件，继续处理下一个。' if has_next else '此文件未完成，详见下方汇总。'
    console.print(Text(f'    {status}', style='ui.muted'))
