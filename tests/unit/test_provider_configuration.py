import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
from unittest.mock import Mock

import httpx
import pytest

from build_llm import copy_llm_configuration
from core.client.llm.config import Provider, ensure_provider_file, load_catalog
from core.client.llm.provider import HTTPTextProvider

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def directory(tmp_path):
    destination = tmp_path / "LLM"
    destination.mkdir()
    for name in ("providers.template.toml", "presets.toml"):
        shutil.copyfile(ROOT / "LLM" / name, destination / name)
    return destination


def test_template_fallback_and_first_edit_never_overwrite_local_key(directory):
    catalog = load_catalog(directory)
    assert catalog.providers["gemini"].model == "gemini-3.5-flash-lite"
    assert catalog.presets["correct_asr"].provider == "gemini"
    assert catalog.presets["translate"].provider == "gemini"
    assert not (directory / "providers.toml").exists()
    local = ensure_provider_file(directory)
    local.write_text(
        local.read_text(encoding="utf-8").replace('api_key = ""', 'api_key = "synthetic-key"'),
        encoding="utf-8",
    )
    original = local.read_bytes()
    assert ensure_provider_file(directory).read_bytes() == original
    provider = load_catalog(directory).providers["gemini"]
    assert provider.api_key == "synthetic-key"
    assert "synthetic-key" not in repr(provider)


def test_gemini_template_request_uses_google_endpoint_and_local_key(directory, monkeypatch):
    local = ensure_provider_file(directory)
    local.write_text(
        local.read_text(encoding="utf-8").replace('api_key = ""', 'api_key = "synthetic-key"'),
        encoding="utf-8",
    )
    provider = load_catalog(directory).providers["gemini"]
    requests = []
    original_client = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        assert (
            str(request.url)
            == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        )
        assert request.headers["authorization"] == "Bearer synthetic-key"
        assert json.loads(request.content)["model"] == "gemini-3.5-flash-lite"
        return httpx.Response(200, json={"choices": [{"message": {"content": "corrected"}}]})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw),
    )
    result = asyncio.run(
        HTTPTextProvider().complete(provider, [{"role": "user", "content": "synthetic"}], 0, 2048)
    )
    assert result == "corrected" and len(requests) == 1


def test_empty_local_key_is_rejected_before_network(directory, monkeypatch):
    client = Mock(side_effect=AssertionError("no network"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(ValueError, match="local configuration is empty"):
        asyncio.run(
            HTTPTextProvider().complete(load_catalog(directory).providers["gemini"], [], 0, 10)
        )
    client.assert_not_called()


def test_explicit_environment_key_takes_precedence(monkeypatch):
    original_client = httpx.AsyncClient
    monkeypatch.setenv("TEST_GEMINI_KEY", "environment-key")

    def handle(request):
        assert request.headers["authorization"] == "Bearer environment-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw),
    )
    provider = Provider(
        "x", "openai", "https://example.invalid", "test", "TEST_GEMINI_KEY", api_key="local-key"
    )
    assert asyncio.run(HTTPTextProvider().complete(provider, [], 0, 10)) == "ok"


def test_package_copies_only_public_templates(directory, tmp_path):
    (directory / "providers.toml").write_text('api_key = "private-sentinel"', encoding="utf-8")
    (directory / "private-backup.toml").write_text('api_key = "private-sentinel"', encoding="utf-8")
    destination = tmp_path / "release"
    copy_llm_configuration(directory.parent, destination)
    files = list((destination / "LLM").iterdir())
    assert {p.name for p in files} == {"providers.toml", "providers.template.toml", "presets.toml"}
    assert all("private-sentinel" not in p.read_text(encoding="utf-8") for p in files)
    assert (
        tomllib.loads((destination / "LLM/providers.toml").read_text(encoding="utf-8"))[
            "providers"
        ]["gemini"]["api_key"]
        == ""
    )


def test_package_rejects_key_in_template(directory, tmp_path):
    path = directory / "providers.template.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('api_key = ""', 'api_key = "synthetic-key"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must not contain API keys"):
        copy_llm_configuration(directory.parent, tmp_path / "release")
    assert not (tmp_path / "release/LLM").exists()


def test_private_provider_is_ignored_but_template_is_not():
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "LLM/providers.toml", "LLM/providers.template.toml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.splitlines() == ["LLM/providers.toml"]


def test_specs_do_not_link_private_llm_directory():
    import ast

    for name in ("build.spec", "build-client.spec"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "link_folders" for t in node.targets)
        ]
        assert assignments
        assert all("LLM" not in ast.literal_eval(node.value) for node in assignments)
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "copy_llm_configuration"
            for node in ast.walk(tree)
        )
