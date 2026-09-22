"""Validated, debounced configuration reload without executing edited Python.

The application owns publication and its task boundary. This module only prepares
detached candidates; resource settings remain at their startup values.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import math
import os
import operator
from pathlib import Path
from types import SimpleNamespace


CLIENT_LIVE = frozenset(
    """save_audio audio_dir audio_name_len language
paste restore_clip paste_apps enter_apps trash_punc trash_punc_thresh trash_punc_apps
traditional_convert traditional_locale save_transcripts transcript_dir
transcript_save_original save_llm_records caret_context_enabled
caret_context_before_chars caret_context_after_chars llm_enabled
llm_correction_enabled llm_translation_enabled llm_default_preset
mic_seg_duration mic_seg_overlap mic_io_timeout mic_result_timeout
file_seg_duration file_seg_overlap file_io_timeout file_result_timeout
file_max_inflight_chunks""".split()
)
SERVER_LIVE = frozenset({"format_num", "format_spell"})


class CandidateError(ValueError):
    """A content-free error safe for diagnostics."""


def read_settings(source: bytes, path: Path) -> dict:
    """Interpret the declarative subset used by the shipped Python templates.

    No imports, arbitrary calls, descriptors or edited class bodies are executed.
    Custom executable configuration continues to work at startup, but requires a
    restart instead of being executed again in a live application.
    """
    env = {"__file__": str(path), "Path": Path}
    calls = {
        "Path": Path,
        "os.environ.get": os.environ.get,
        "os.path.dirname": os.path.dirname,
        "os.path.abspath": os.path.abspath,
        "os.path.join": os.path.join,
    }
    binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    def expr(node, scope):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name) and node.id in scope:
            return scope[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [expr(item, scope) for item in node.elts]
            return tuple(values) if isinstance(node, ast.Tuple) else values
        if isinstance(node, ast.Dict) and all(node.keys):
            return {expr(k, scope): expr(v, scope) for k, v in zip(node.keys, node.values)}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = expr(node.operand, scope)
            return -value if isinstance(node.op, ast.USub) else +value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](expr(node.left, scope), expr(node.right, scope))
        if isinstance(node, ast.Attribute) and not node.attr.startswith("_"):
            value = expr(node.value, scope)
            if isinstance(value, SimpleNamespace):
                return vars(value)[node.attr]
            if isinstance(value, Path) and node.attr in {"name", "parent", "stem"}:
                return getattr(value, node.attr)
        if isinstance(node, ast.Call) and not node.keywords:
            name = ast.unparse(node.func)
            args = [expr(arg, scope) for arg in node.args]
            if name in calls:
                return calls[name](*args)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "as_posix" and not args:
                value = expr(node.func.value, scope)
                if isinstance(value, Path):
                    return value.as_posix()
        raise CandidateError("Unsupported Python expression; restart required")

    def assignments(body, scope, *, module=False):
        result = {}
        for node in body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            if module and isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                )
                if all(name in {"os", "pathlib", "collections.abc"} for name in names):
                    continue
            if (
                module
                and isinstance(node, ast.ClassDef)
                and not node.bases
                and not node.decorator_list
            ):
                values = assignments(node.body, dict(scope))
                scope[node.name] = SimpleNamespace(**values)
                result[node.name] = values
                continue
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else ([node.target] if isinstance(node, ast.AnnAssign) else [])
            )
            if len(targets) != 1 or not isinstance(targets[0], ast.Name) or node.value is None:
                raise CandidateError("Unsupported Python statement; restart required")
            key = targets[0].id
            if key in result:
                raise CandidateError("Duplicate configuration assignment")
            try:
                value = expr(node.value, scope)
            except CandidateError:
                raise
            except Exception:
                raise CandidateError("Invalid configuration expression") from None
            scope[key] = result[key] = value
        return result

    try:
        return assignments(ast.parse(source.decode("utf-8-sig")).body, env, module=True)
    except (SyntaxError, UnicodeError):
        raise CandidateError("Invalid or incomplete Python file") from None


def validate_settings(values, defaults, section):
    """Check the entire candidate, including settings held for restart."""
    for group, fields in defaults.items():
        if not isinstance(fields, dict):
            continue
        candidate = values[group]
        for name, default in fields.items():
            value = candidate[name]
            valid = True
            if name == "port":
                valid = type(value) in (str, int)
            elif name == "input_device":
                valid = value is None or type(value) in (str, int)
            elif name == "llm_default_preset":
                valid = value is None or isinstance(value, str)
            elif default is None:
                valid = value is None or (type(value) is int and value > 0)
            elif isinstance(default, bool):
                valid = type(value) is bool
            elif isinstance(default, (int, float)):
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
                if (
                    type(default) is int
                    and not any(
                        part in name for part in ("duration", "overlap", "timeout", "seconds")
                    )
                    and name != "threshold"
                ):
                    valid = valid and type(value) is int
            elif isinstance(default, (list, tuple)):
                valid = isinstance(value, (list, tuple))
            elif isinstance(default, Path):
                valid = isinstance(value, (str, Path))
            else:
                valid = isinstance(value, type(default))
            if not valid:
                raise CandidateError(f"Invalid type or range: {group}.{name}")
    cfg = values[section]
    if not str(cfg["port"]).isdigit() or not 1 <= int(cfg["port"]) <= 65535:
        raise CandidateError("Invalid port")
    if cfg["log_level"] not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise CandidateError("Invalid log_level")
    for name, value in cfg.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if (
                ("timeout" in name or "max" in name or name.endswith("_seconds"))
                and value <= 0
                and name not in {"aligner_idle_timeout"}
            ):
                raise CandidateError(f"Expected positive value: {name}")
    if section == "ClientConfig":
        for prefix in ("mic", "file"):
            duration, overlap = cfg[f"{prefix}_seg_duration"], cfg[f"{prefix}_seg_overlap"]
            if not (0.1 <= duration <= 120 and 0 <= overlap <= 30 and overlap < duration):
                raise CandidateError(f"Invalid segment duration/overlap: {prefix}")
        for name in ("paste_apps", "trash_punc_apps", "file_media_extensions"):
            if not all(isinstance(item, str) for item in cfg[name]):
                raise CandidateError(f"Expected string entries: {name}")
        for name in ("audio_dir", "transcript_dir", "llm_config_dir"):
            if "\0" in cfg[name] or (name != "audio_dir" and not cfg[name].strip()):
                raise CandidateError(f"Invalid directory: {name}")
        audio_path = Path(os.path.expandvars(cfg["audio_dir"])).expanduser()
        if audio_path.drive and not audio_path.is_absolute():
            raise CandidateError("Invalid audio_dir: drive-relative paths are ambiguous")
        if cfg["audio_name_len"] > 200 or cfg["traditional_locale"] not in {
            "zh-hant",
            "zh-tw",
            "zh-hk",
        }:
            raise CandidateError("Invalid audio_name_len or traditional_locale")
        if not cfg["language"] or len(cfg["language"]) > 32:
            raise CandidateError("Invalid language")
        for row in cfg["enter_apps"]:
            if (
                not isinstance(row, (tuple, list))
                or len(row) != 2
                or not isinstance(row[0], str)
                or type(row[1]) not in (int, float)
                or not math.isfinite(row[1])
                or row[1] < 0
            ):
                raise CandidateError("Invalid enter_apps entry")
        for row in cfg["shortcuts"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("key"), str)
                or row.get("type") not in {"keyboard", "mouse"}
                or any(type(row.get(k)) is not bool for k in ("suppress", "hold_mode", "enabled"))
            ):
                raise CandidateError("Invalid shortcuts entry")
        for row in cfg["udp_broadcast_targets"]:
            if (
                not isinstance(row, (tuple, list))
                or len(row) != 2
                or not isinstance(row[0], str)
                or type(row[1]) is not int
                or not 1 <= row[1] <= 65535
            ):
                raise CandidateError("Invalid UDP target")
    else:
        import ipaddress

        mode = cfg["network_mode"]
        try:
            loopback = (
                cfg["addr"].lower() == "localhost" or ipaddress.ip_address(cfg["addr"]).is_loopback
            )
        except ValueError:
            loopback = False
        if mode not in {"local", "lan"} or (mode == "local" and not loopback):
            raise CandidateError("Invalid network mode/address")
        if mode == "lan" and len(cfg["auth_token"].strip()) < 32:
            raise CandidateError("LAN authentication token is missing or too short")
        if bool(cfg["tls_certfile"]) != bool(cfg["tls_keyfile"]):
            raise CandidateError("TLS requires both certificate and key")
        if cfg["model_type"] not in {"qwen_asr", "fun_asr_nano", "sensevoice", "paraformer"}:
            raise CandidateError("Invalid model_type")
        if (
            cfg["websocket_max_message_bytes"]
            < (cfg["max_message_audio_bytes"] + 2) // 3 * 4 + 65536
        ):
            raise CandidateError("WebSocket message limit cannot contain configured audio")


class ConfigReloader:
    """Prepare stable files off-loop and publish on the application's owner loop."""

    def __init__(self, path, module, template, section, live, report):
        self.path = Path(path)
        self._closed = False
        self.section, self.live = section, live
        self.report = lambda message: None if self._closed else report(message)
        self.target = getattr(module, section)
        self.defaults = {
            name: {
                key: copy.deepcopy(value)
                for key, value in vars(cls).items()
                if not key.startswith("_")
            }
            for name, cls in vars(template).items()
            if isinstance(cls, type) and cls.__module__ == template.__name__
        }
        self.current = {
            group: {
                key: copy.deepcopy(getattr(getattr(module, group, None), key, value))
                for key, value in fields.items()
            }
            for group, fields in self.defaults.items()
        }
        self.metadata = {
            key: getattr(module, key) for key in ("BASE_DIR", "__version__") if hasattr(module, key)
        }
        self.required = {
            group: {
                key
                for key in vars(getattr(module, group, SimpleNamespace()))
                if not key.startswith("_")
            }
            for group in self.defaults
            if hasattr(module, group)
        }
        self.seen = None
        self.processed = None
        self.pending = None
        self.task = None
        self.last_error = None

    def poll(self):
        """Require identical bytes on two polls; newer edits revoke pending data."""
        if self._closed:
            return
        try:
            source = self.path.read_bytes()
            if self._closed:
                return
            if len(source) > 1024 * 1024:
                raise CandidateError("Configuration file too large")
            digest = hashlib.sha256(source).digest()
            if digest != self.seen:
                self.seen, self.pending = digest, None
                self.processed = None
                return
            if digest == self.processed:
                return
            self.processed = digest
            parsed = read_settings(source, self.path)
            for group, fields in self.required.items():
                if group not in parsed or not fields <= parsed[group].keys():
                    raise CandidateError("Incomplete configuration: existing fields were removed")
            values = copy.deepcopy(self.defaults)
            for group in values:
                if group not in parsed:
                    if group in self.required:
                        raise CandidateError("Incomplete configuration: missing class")
                    continue
                unknown = parsed[group].keys() - values[group].keys()
                if unknown:
                    raise CandidateError("Unknown configuration field; restart required")
                values[group].update(parsed[group])
            validate_settings(values, self.defaults, self.section)
            self.last_error = None
            changes = {
                name: value
                for name, value in values[self.section].items()
                if name in self.live and value != self.current[self.section][name]
            }
            restart = [
                f"{g}.{k}"
                for g in values
                for k in values[g]
                if (g != self.section or k not in self.live) and values[g][k] != self.current[g][k]
            ]
            restart.extend(key for key, value in self.metadata.items() if parsed.get(key) != value)
            if restart:
                self.report(
                    "Restart required (resource settings unchanged): " + ", ".join(sorted(restart))
                )
            if changes:
                self.pending = changes
                self.report(
                    "Configuration validated; waiting for a safe task boundary: "
                    + ", ".join(sorted(changes))
                )
            self.required = {g: set(f) for g, f in parsed.items() if isinstance(f, dict)}
        except Exception as exc:
            self.pending = None
            if isinstance(exc, OSError):
                self.seen = self.processed = None
            message = str(exc) if isinstance(exc, CandidateError) else type(exc).__name__
            if self.last_error != (self.seen, message):
                self.report(
                    "Configuration reload rejected; last valid settings retained: " + message
                )
                self.last_error = (self.seen, message)

    def apply(self):
        """Caller must hold its task-admission lock; no await or I/O here."""
        if not self.pending:
            return ()
        changes, self.pending = self.pending, None
        for name, value in changes.items():
            setattr(self.target, name, value)
        self.current[self.section].update(copy.deepcopy(changes))
        return tuple(sorted(changes))

    async def watch(self, publish):
        while True:
            await asyncio.to_thread(self.poll)
            if not self._closed:
                publish()
            await asyncio.sleep(1)

    def start(self, publish):
        self.task = asyncio.create_task(self.watch(publish))

    async def close(self):
        self._closed = True
        self.pending = None
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
