"""静态 Provider 与文本预设配置；不会导入或执行旧角色文件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import tomllib


@dataclass(frozen=True)
class Provider:
    id: str
    kind: str
    base_url: str
    model: str
    api_key_env: str = ""
    timeout: float = 30.0
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    provider: str
    system_prompt: str
    triggers: tuple[str, ...] = ()
    use_caret_context: bool = False
    temperature: float = 0.0
    max_tokens: int = 2048


@dataclass(frozen=True)
class Catalog:
    providers: dict[str, Provider]
    presets: dict[str, Preset]

    def select(self, text: str, default: str | None) -> tuple[Preset | None, str]:
        # 优先匹配最长口令；显示名称不参与路由。
        candidates = sorted(
            ((trigger, preset) for preset in self.presets.values() for trigger in preset.triggers),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for trigger, preset in candidates:
            if text.startswith(trigger):
                content = text[len(trigger) :].lstrip("：，。,. :")
                if content:
                    return preset, content
        if default is None:
            return None, text
        if not isinstance(default, str) or default not in self.presets:
            raise ValueError("llm_default_preset must be one preset ID or None")
        return self.presets[default], text


def _text(data: dict, name: str, default=None) -> str:
    value = data.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def load_catalog(directory: Path) -> Catalog:
    from urllib.parse import urlsplit

    providers = {}
    presets = {}
    provider_path = directory / "providers.toml"
    if not provider_path.exists():
        provider_path = directory / "providers.template.toml"
    with provider_path.open("rb") as stream:
        provider_data = tomllib.load(stream).get("providers", {})
    with (directory / "presets.toml").open("rb") as stream:
        preset_data = tomllib.load(stream).get("presets", {})
    for identifier, data in provider_data.items():
        kind = _text(data, "kind")
        if kind not in {"ollama", "openai"}:
            raise ValueError("Provider kind must be ollama or openai")
        url = _text(data, "base_url").rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Provider URL must be HTTP(S), without embedded credentials")
        timeout = float(data.get("timeout", 30))
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("Provider timeout must be between 0 and 300 seconds")
        key_env = data.get("api_key_env", "")
        if not isinstance(key_env, str):
            raise ValueError("api_key_env must be an environment variable name")
        key = data.get("api_key")
        if key is not None and not isinstance(key, str):
            raise ValueError("api_key must be a string")
        providers[identifier] = Provider(
            identifier,
            kind,
            url,
            _text(data, "model"),
            key_env,
            timeout,
            key.strip() if key is not None else None,
        )
    triggers_seen = set()
    for identifier, data in preset_data.items():
        provider = _text(data, "provider")
        if provider not in providers:
            raise ValueError(f"Unknown provider in preset {identifier}")
        triggers = data.get("triggers", [])
        if not isinstance(triggers, list) or any(
            not isinstance(t, str) or not t.strip() for t in triggers
        ):
            raise ValueError("triggers must be a list of non-empty strings")
        triggers = tuple(t.strip() for t in triggers)
        for trigger in triggers:
            if trigger in triggers_seen:
                raise ValueError("Duplicate preset trigger")
            triggers_seen.add(trigger)
        context = data.get("use_caret_context", False)
        if not isinstance(context, bool):
            raise ValueError("use_caret_context must be boolean")
        temperature = float(data.get("temperature", 0))
        tokens = data.get("max_tokens", 2048)
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= 32768:
            raise ValueError("max_tokens must be between 1 and 32768")
        presets[identifier] = Preset(
            identifier,
            _text(data, "name"),
            provider,
            _text(data, "system_prompt"),
            triggers,
            context,
            temperature,
            tokens,
        )
    return Catalog(providers, presets)


def ensure_provider_file(directory: Path) -> Path:
    """仅首次编辑时创建本机副本；绝不覆盖已有的配置或凭据。"""
    path = directory / "providers.toml"
    if path.exists():
        return path
    template = (directory / "providers.template.toml").read_bytes()
    try:
        with path.open("xb") as stream:
            stream.write(template)
    except FileExistsError:
        pass
    return path
