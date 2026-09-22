"""File-task feedback with stable failure IDs separate from display labels."""

from core.i18n import tr

from rich.text import Text


FAILURE_LABELS = {
    "recognition_failed": (
        "file.failure.recognition_failed.reason",
        "file.failure.recognition_failed.action",
    ),
    "decode_failed": ("file.failure.decode_failed.reason", "file.failure.decode_failed.action"),
    "missing_file": ("file.failure.missing_file.reason", "file.failure.missing_file.action"),
    "decoder_unavailable": (
        "file.failure.decoder_unavailable.reason",
        "file.failure.decoder_unavailable.action",
    ),
    "connection_failed": (
        "file.failure.connection_failed.reason",
        "file.failure.connection_failed.action",
    ),
    "timeout": ("file.failure.timeout.reason", "file.failure.timeout.action"),
    "invalid_result": ("file.failure.invalid_result.reason", "file.failure.invalid_result.action"),
    "output_failed": ("file.failure.output_failed.reason", "file.failure.output_failed.action"),
    "unexpected": ("file.failure.unexpected.reason", "file.failure.unexpected.action"),
}


def print_file_failure(console, file, code, *, has_next):
    reason_id, action_id = FAILURE_LABELS.get(code, FAILURE_LABELS["unexpected"])
    reason, action = tr(reason_id), tr(action_id)
    console.print(Text.assemble((tr("file.failed"), "ui.error"), "  ", (file.name, "ui.value")))
    console.print(Text.assemble((tr("file.reason"), "ui.label"), (reason, "ui.value")))
    console.print(Text.assemble((tr("file.action"), "ui.label"), (action, "ui.value")))
    status = tr("file.next") if has_next else tr("file.last_failed")
    console.print(Text(f"    {status}", style="ui.muted"))
