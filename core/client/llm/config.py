"""Load static providers and text presets without executing legacy role files."""

from __future__ import annotations

from core.i18n import Notice

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
        # Prefer the longest trigger; display names do not participate in routing.
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
            raise ValueError(Notice('validation.config.llm_default_preset_must_be_one_preset_id'))
        return self.presets[default], text


def _text(data: dict, name: str, default=None) -> str:
    value = data.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(Notice('validation.config.must_be_a_non_empty_string', value0=name))
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
            raise ValueError(Notice('validation.config.provider_kind_must_be_ollama_or_openai'))
        url = _text(data, "base_url").rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(Notice('validation.config.provider_url_must_be_http_s_without_embedded'))
        timeout = float(data.get("timeout", 30))
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError(Notice('validation.config.provider_timeout_must_be_between_and_seconds'))
        key_env = data.get("api_key_env", "")
        if not isinstance(key_env, str):
            raise ValueError(Notice('validation.config.api_key_env_must_be_an_environment_variable'))
        key = data.get("api_key")
        if key is not None and not isinstance(key, str):
            raise ValueError(Notice('validation.config.api_key_must_be_a_string'))
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
            raise ValueError(Notice('validation.config.unknown_provider_in_preset', value0=identifier))
        triggers = data.get("triggers", [])
        if not isinstance(triggers, list) or any(
            not isinstance(t, str) or not t.strip() for t in triggers
        ):
            raise ValueError(Notice('validation.config.triggers_must_be_a_list_of_non_empty'))
        triggers = tuple(t.strip() for t in triggers)
        for trigger in triggers:
            if trigger in triggers_seen:
                raise ValueError(Notice('validation.config.duplicate_preset_trigger'))
            triggers_seen.add(trigger)
        context = data.get("use_caret_context", False)
        if not isinstance(context, bool):
            raise ValueError(Notice('validation.config.use_caret_context_must_be_boolean'))
        temperature = float(data.get("temperature", 0))
        tokens = data.get("max_tokens", 2048)
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError(Notice('validation.config.temperature_must_be_between_and'))
        if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= 32768:
            raise ValueError(Notice('validation.config.max_tokens_must_be_between_and'))
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
    """Create a local copy on first edit without replacing existing credentials."""
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
