# Interface language

The interface supports English (`en`) and Simplified Chinese (`zh-CN`). The default
`auto` follows the Windows UI language: Simplified Chinese for mainland China and
Singapore, otherwise English. Unsupported startup values and missing translations
fall back to English. Older configurations without `ui_language` use `auto`.

In the client tray, select **Settings > Interface language > English / 简体中文 /
System language** (`跟随系统` in Chinese). The preference is saved in `ClientConfig.ui_language` in
`config_client.py`. Only that assignment is changed; other settings and comments
are preserved. If saving fails, the effective language stays unchanged and a
notification explains the failure.

You can also edit the configuration directly:

```python
class ClientConfig:
    ui_language = 'zh-CN'  # 'auto', 'en', or 'zh-CN'
```

The client and server in the same installation follow `ClientConfig.ui_language`.
The server uses the saved preference at startup, even if the client is not running,
and watches `config_client.py` for subsequent language-menu or direct edits.
`ServerConfig.ui_language` is a compatibility fallback when the local client
configuration has no `ui_language` field. Neither configuration file is rewritten
by the server. Clients in other directories or on other machines do not control
this server's terminal language over the network.

Start the updated application once. Later edits use the existing
[configuration reload](../reference/configuration.md) mechanism. Client changes normally
apply within one to two seconds after pending recording, ASR, LLM, output and
archive work finishes, or between files in a batch. A language menu checkmark
shows the effective preference. Close and reopen the menu after a change to see
updated labels and tooltips. Already displayed transient notifications finish in
their original language. The server normally follows saved changes within one to
two seconds after its service has started. It validates the complete client
candidate but publishes only a detached language setting; other client options
and active recognition tasks are unaffected. During an active client task, the
server may update first because the client waits for its safe task boundary.
Invalid or incomplete live edits retain the last valid preference.
Console history is not rewritten: a language change can show a pending notice in
the old language followed by an applied notice in the new language. Subsequent
recording and receiving spinners resolve the current language when they start.
ASR and aligner processes read a shared locale value for subsequent output; a
server language change does not reload models or alter their configuration snapshot.

The preference controls tray menus and tooltips, recording/processing hints,
shared dialog buttons, file progress and summaries, subtitle rebuild feedback,
controlled LLM failures, client CLI help and parser errors, configuration and
protocol validation explanations, server status, model loading/progress notices,
and project-owned diagnostic messages displayed in the terminal. Maintained
cleanup/subtitle utilities and UI demonstration labels use the same resources.
The language names `English` and `简体中文` deliberately retain their autonyms.

Diagnostic files retain English messages. The console formatter localizes a copy
of each controlled record, so handler order cannot change archive language.
Native llama/ONNX/OS output and arbitrary third-party exception details remain
verbatim: they are external diagnostics, not project-owned interface strings.
Command names, switches, configuration keys, provider/model names and error codes
remain stable. Developer build scripts and comments/docstrings follow the [internal language policy](internal-language.md). Copied upstream export tools retain their original text. These are separate from runtime UI catalogs.

`ClientConfig.language` still selects the ASR language. LLM prompts, translation
targets, user-defined preset names, recognized text, clipboard contents, file
names and saved transcripts are not translated by the interface preference.

## Resource maintenance

`core/i18n/en.py` is the English fallback catalog; `core/i18n/zh_cn.py` is the
Simplified Chinese catalog. Stable message IDs and named placeholders separate
presentation from task IDs, protocol enums, preset IDs and shortcuts. `tr()`
formats the selected message; `lazy()` resolves menu text when the menu is built.
Both catalogs are statically imported Python modules, so they follow ordinary
source and PyInstaller module collection without external locale files.

Use `Notice` for logging and controlled exception arguments. It carries an
English diagnostic alongside a localizable message, supports nested notices and
pickle round trips, and preserves formatting parameters. Use `tr` at direct
display boundaries and `lazy` for menus. `core/i18n/logging.py` localizes console
records without mutating shared records. `core/i18n/argparse.py` adapts the
Python 3.11 parser's complete error templates without global gettext changes.
LLM exceptions preserve English diagnostics and render only controlled reasons
in the selected UI language.
Never translate arbitrary user content or provider response bodies.

Regression checks compare locale key sets, duplicate IDs, format and logging
placeholders, check literal IDs across maintained engine wrappers as well as UI,
exercise real pystray callback adaptation, and cover configuration boundaries,
safe persistence, fallback, CLI, controlled errors and content preservation.
Source guards reject embedded English/Chinese sentences at display/logging sinks
and hidden Chinese labels assigned before display. Explicit exceptions preserve
recognition rules, prompts, ASR language aliases, multilingual rendering fixtures
and the existing recognition-result abort sentinel. A synthetic spawned worker
verifies language changes without loading models.
The [manual acceptance checklist](../validation/P1-multilingual-interface.md)
records desktop checks and the current acceptance status.
