# Configuration templates

Use these tracked templates as the source for configuration fields, defaults, and version:

| Template | Local runtime copy |
| --- | --- |
| [config_client_template.py](config_client_template.py) | `config_client.py` in the application root |
| [config_server_template.py](config_server_template.py) | `config_server.py` in the application root |

The application reads the root copies. Git ignores them so device choices, model paths, shortcuts, and other local settings stay private. Templates and local copies are separate files; do not move the templates.

## Initialize missing configuration

Run from the repository root:

```powershell
if (!(Test-Path config_client.py)) { Copy-Item config_templates/config_client_template.py config_client.py }
if (!(Test-Path config_server.py)) { Copy-Item config_templates/config_server_template.py config_server.py }
```

These commands preserve existing files. During upgrades, merge required fields or structural changes while retaining local values. Review differences locally; configuration values can be sensitive:

```powershell
git diff --no-index -- config_templates/config_client_template.py config_client.py
git diff --no-index -- config_templates/config_server_template.py config_server.py
```

An exit code of 1 from `git diff --no-index` means differences were found.

## Maintain defaults

Change the tracked templates when adding settings or updating their descriptions. Keep backward-compatible defaults in the implementation. Never copy local configuration into a template. PyInstaller also copies these templates into packages; [LLM credentials](../docs/user/text-actions.md) use a separate ignored TOML file.

See [configuration and reload behavior](../docs/reference/configuration.md) for live settings, restart requirements, and recording storage.
