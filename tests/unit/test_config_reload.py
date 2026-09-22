"""Real template parsing, transactional reload and task-boundary regressions."""

import asyncio
import copy
import pickle
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.config_reload import ConfigReloader, CLIENT_LIVE, SERVER_LIVE
from config_templates import config_client_template, config_server_template


def make_reloader(tmp_path, *, server=False):
    template = config_server_template if server else config_client_template
    section = "ServerConfig" if server else "ClientConfig"
    module = SimpleNamespace(
        **{
            name: type(
                name,
                (),
                {k: copy.deepcopy(v) for k, v in vars(cls).items() if not k.startswith("_")},
            )
            for name, cls in vars(template).items()
            if isinstance(cls, type) and cls.__module__ == template.__name__
        }
    )
    path = tmp_path / ("config_server.py" if server else "config_client.py")
    path.write_bytes(Path(template.__file__).read_bytes())
    messages = []
    reload = ConfigReloader(
        path, module, template, section, SERVER_LIVE if server else CLIENT_LIVE, messages.append
    )
    reload.poll()
    reload.poll()
    assert reload.last_error is None
    assert reload.pending is None
    return reload, getattr(module, section), messages


def edit(reload, old, new):
    reload.path.write_text(
        reload.path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )
    reload.poll()
    assert reload.pending is None
    reload.poll()


def test_debounce_atomic_apply_and_restart_fields(tmp_path):
    reload, config, messages = make_reloader(tmp_path)
    edit(reload, "save_audio = False", "save_audio = True\n    audio_dir = 'duplicate'")
    assert reload.pending is None
    assert config.save_audio is False
    assert "Duplicate" in messages[-1]
    edit(reload, "save_audio = True\n    audio_dir = 'duplicate'", "save_audio = True")
    assert config.save_audio is False
    assert reload.apply() == ("save_audio",)
    assert config.save_audio is True
    edit(reload, "port = '6016'", "port = '6017'")
    assert config.port == "6016"
    assert "Restart required" in messages[-1]


@pytest.mark.parametrize(
    "replacement",
    [
        "save_audio = 'yes'",
        "save_audio =",
        "save_audio = True\n    file_seg_overlap = 1000",
        "save_audio = True\n    unexpected_option = False",
        "save_audio = __import__('os').system('must not run')",
    ],
)
def test_invalid_whole_candidate_never_partially_applies(tmp_path, replacement):
    reload, config, messages = make_reloader(tmp_path)
    edit(reload, "save_audio = False", replacement)
    assert not reload.apply()
    assert config.save_audio is False
    before = len(messages)
    reload.poll()
    assert len(messages) == before
    assert "must not run" not in "\n".join(messages)


def test_pending_candidate_is_revoked_by_new_partial_save_and_recovers(tmp_path):
    reload, config, _ = make_reloader(tmp_path)
    edit(reload, "save_audio = False", "save_audio = True")
    good = reload.path.read_bytes()
    reload.path.write_bytes(b"class ClientConfig:\n    save_audio = False\n")
    reload.poll()
    assert not reload.apply()
    reload.poll()
    assert config.save_audio is False
    reload.path.write_bytes(good)
    reload.poll()
    reload.poll()
    assert reload.apply() == ("save_audio",)


def test_missing_file_preserves_config_and_can_retry_same_candidate(tmp_path):
    reload, config, _ = make_reloader(tmp_path)
    edit(reload, "save_audio = False", "save_audio = True")
    good = reload.path.read_bytes()
    reload.path.unlink()
    reload.poll()
    assert not reload.apply()
    reload.path.write_bytes(good)
    reload.poll()
    reload.poll()
    assert reload.apply() == ("save_audio",)


def test_legacy_missing_field_gets_default_but_removed_fields_reject(tmp_path):
    reload, config, _ = make_reloader(tmp_path)
    del config.audio_dir
    reload.required["ClientConfig"].remove("audio_dir")
    edit(reload, "    audio_dir = ''\n", "")
    assert reload.last_error is None
    edit(reload, "    save_audio = False", "")
    assert reload.last_error is not None


def test_legacy_server_without_unused_engine_classes_can_reload(tmp_path):
    reload, config, _ = make_reloader(tmp_path, server=True)
    source = reload.path.read_text(encoding="utf-8")
    reload.path.write_text(source[: source.index("class ModelDownloadLinks:")], encoding="utf-8")
    reload.required = {"ServerConfig": reload.required["ServerConfig"]}
    reload.poll()
    reload.poll()
    assert reload.last_error is None
    edit(reload, "format_spell = True", "format_spell = False")
    assert reload.apply() == ("format_spell",)
    assert config.format_spell is False


@pytest.mark.parametrize('setting', ['audio', 'language'])
@pytest.mark.parametrize(
    "busy",
    [
        "recording_owner",
        "recording_futures",
        "recording_tasks",
        "dictation_uploads",
        "task_contexts",
        "_file_active",
    ],
)
def test_client_waits_for_capture_through_final_output(tmp_path, monkeypatch, busy, setting):
    from core.client.app import CapsWriterClient
    from core.client.state import ClientState

    reload, config, _ = make_reloader(tmp_path)
    monkeypatch.setattr("core.client.app.Config", config)
    app = CapsWriterClient.__new__(CapsWriterClient)
    app.state = ClientState()
    app._stopping = app._file_active = False
    app.config_reload = reload
    app._report_config = Mock()
    app.llm = SimpleNamespace(start=Mock())
    refresh = Mock()
    monkeypatch.setattr('core.ui.tray.refresh_language', refresh)
    if setting == 'language':
        edit(reload, "ui_language = 'auto'", "ui_language = 'zh-CN'")
    else:
        edit(reload, "save_audio = False", "save_audio = True")
    owner = app if busy == "_file_active" else app.state
    original = getattr(owner, busy)
    setattr(owner, busy, True)
    app.apply_config_reload()
    assert config.save_audio is False
    assert config.ui_language == 'auto'
    setattr(owner, busy, original)
    app.apply_config_reload()
    if setting == 'language':
        from core.i18n import get_language
        assert config.ui_language == get_language() == 'zh-CN'
        refresh.assert_called_once()
    else:
        assert config.save_audio is True


@pytest.mark.parametrize('server', [False, True])
def test_language_reload_rejects_invalid_preference(tmp_path, server):
    reload, config, messages = make_reloader(tmp_path, server=server)
    edit(reload, "ui_language = 'auto'", "ui_language = 'invalid'")
    assert reload.pending is None
    assert 'Invalid ui_language' in messages[-1]
    assert config.ui_language == 'auto'
    edit(reload, "ui_language = 'invalid'", "ui_language = 'en'")
    assert reload.apply() == ('ui_language',)
    assert config.ui_language == 'en'


def test_server_snapshot_survives_reload_and_ipc(tmp_path, monkeypatch):
    from core.protocol import AudioMessage
    from core.server.connection.ws_recv import AudioCache, message_handler
    from core.server.formatter.text_formatter import TextFormatter

    reload, config, _ = make_reloader(tmp_path, server=True)
    monkeypatch.setattr("core.server.connection.ws_recv.Config", config)
    monkeypatch.setattr("core.server.formatter.text_formatter.Config", config)
    first = AudioMessage("first", "file", "", False, 0)
    cache = AudioCache(first)
    edit(reload, "format_num = True", "format_num = False")
    assert reload.apply() == ("format_num",)
    assert AudioCache(first).formatting == (False, True)
    assert cache.formatting == (True, True)
    tasks = []
    app = SimpleNamespace(state=SimpleNamespace(queue_in=SimpleNamespace(put_nowait=tasks.append)))
    first.is_final = True
    asyncio.run(message_handler(SimpleNamespace(id="socket"), first, cache, app))
    task = pickle.loads(pickle.dumps(tasks[0]))
    assert task.formatting == (True, True)
    formatter = TextFormatter()
    monkeypatch.setattr(
        "core.server.formatter.text_formatter.chinese_to_num", lambda _: "converted"
    )
    assert formatter.format("synthetic", formatting=task.formatting) == "converted"
    assert formatter.format("synthetic", formatting=(False, False)) == "synthetic"


def test_watcher_stops_on_shutdown(tmp_path):
    async def run():
        reload, _, _ = make_reloader(tmp_path)
        publish = Mock()
        reload.start(publish)
        await asyncio.sleep(0.05)
        await reload.close()
        count = publish.call_count
        await asyncio.sleep(0.05)
        assert publish.call_count == count
        assert reload.task.done()

    asyncio.run(run())
