"""Shared editor validation, conflicts, privacy and task-boundary publication."""

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config_templates import config_client_template, config_server_template
from core.config_reload import CandidateError, ConfigReloader, CLIENT_LIVE, SERVER_LIVE
from core.settings import SettingsConflict, SettingsService


def make_service(tmp_path, *, server=False):
    template = config_server_template if server else config_client_template
    section = 'ServerConfig' if server else 'ClientConfig'
    module = SimpleNamespace(**{
        name: SimpleNamespace(**{
            key: copy.deepcopy(value) for key, value in vars(cls).items() if not key.startswith('_')
        }) for name, cls in vars(template).items()
        if isinstance(cls, type) and cls.__module__ == template.__name__
    })
    path = tmp_path / ('config_server.py' if server else 'config_client.py')
    path.write_bytes(Path(template.__file__).read_bytes())
    reloader = ConfigReloader(
        path, module, template, section, SERVER_LIVE if server else CLIENT_LIVE, Mock(),
    )
    return SettingsService(reloader), getattr(module, section)


def publish(service):
    service.reloader.poll()
    service.reloader.poll()
    return service.reloader.apply()


@pytest.mark.parametrize('server', [False, True])
def test_preview_saved_effective_and_restart_are_distinct(tmp_path, server):
    service, config = make_service(tmp_path, server=server)
    before = service.read()
    original = service.path.read_bytes()
    preview = service.validate({'ui_language': 'zh-CN', 'port': '6137'}, revision=before.revision)
    assert preview.revision == before.revision
    assert preview.pending == (service.section + '.ui_language',)
    assert service.section + '.port' in preview.restart_required
    assert service.path.read_bytes() == original
    saved = service.save({'ui_language': 'zh-CN', 'port': '6137'}, revision=preview.revision)
    assert saved.revision != before.revision
    assert saved.saved[service.section]['ui_language'] == 'zh-CN'
    assert saved.effective[service.section]['ui_language'] == config.ui_language == 'auto'
    assert publish(service) == ('ui_language',)
    after = service.read()
    assert after.pending == ()
    assert after.effective[service.section]['ui_language'] == config.ui_language == 'zh-CN'
    assert config.port != '6137'
    assert service.section + '.port' in after.restart_required
    # Callers may edit detached values without corrupting shared application state.
    after.effective[service.section]['ui_language'] = 'en'
    assert service.read().effective[service.section]['ui_language'] == 'zh-CN'


@pytest.mark.parametrize('changes', [
    {'save_audio': 'true'}, {'mic_seg_overlap': 999}, {'unknown': False},
    {'ui_language': 'invalid'}, {'port': 70000}, {'save_audio': True, 'paste_apps': [3]},
    [], {'mic_result_timeout': float('nan')},
])
def test_invalid_edits_never_change_file_or_runtime(tmp_path, changes):
    service, config = make_service(tmp_path)
    original = service.path.read_bytes()
    with pytest.raises(CandidateError):
        service.save(changes, revision=service.read().revision)
    assert service.path.read_bytes() == original
    assert config.save_audio is False
    assert not service.reloader.pending
    assert not list(tmp_path.glob('.settings-*'))


def test_external_edits_reject_stale_editor_and_preserve_last_effective(tmp_path):
    service, config = make_service(tmp_path)
    revision = service.read().revision
    service.path.write_bytes(service.path.read_bytes() + b'\n# Another editor\n')
    with pytest.raises(SettingsConflict):
        service.save({'save_audio': True}, revision=revision)
    broken = b"raise AssertionError('private-sentinel-must-not-execute')\n"
    service.path.write_bytes(broken)
    snapshot = service.read()
    assert snapshot.error and snapshot.saved is None and snapshot.pending is None
    assert snapshot.effective['ClientConfig']['save_audio'] is False
    with pytest.raises(CandidateError) as error:
        service.save({'save_audio': True}, revision=snapshot.revision)
    assert 'private-sentinel' not in str(error.value)
    assert service.path.read_bytes() == broken
    assert config.save_audio is False


def test_invalid_unchanged_field_also_rejects_candidate(tmp_path):
    service, _ = make_service(tmp_path)
    source = service.path.read_bytes().replace(b"port = '6016'", b"port = '70000'")
    service.path.write_bytes(source)
    with pytest.raises(CandidateError):
        service.save({'save_audio': True}, revision=service.read().revision)
    assert service.path.read_bytes() == source


def test_two_editors_with_same_revision_have_only_one_winner(tmp_path):
    service, _ = make_service(tmp_path)
    revision = service.read().revision
    barrier = Barrier(2)

    def save(changes):
        editor = SettingsService.standalone(service.path)
        barrier.wait(timeout=3)
        try:
            editor.save(changes, revision=revision)
            return 'saved'
        except SettingsConflict:
            return 'conflict'

    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(save, change) for change in ({'save_audio': True}, {'ui_language': 'en'})]
        assert sorted(job.result(timeout=5) for job in jobs) == ['conflict', 'saved']
    assert service.read().error is None


def test_external_write_during_save_is_detected_and_temp_is_removed(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path)
    original = service.path.read_bytes()
    external = original + b'\n# External edit during temporary-file write\n'
    monkeypatch.setattr('core.settings.os.fsync', lambda _: service.path.write_bytes(external))
    with pytest.raises(SettingsConflict):
        service.save({'save_audio': True}, revision=service.read().revision)
    assert service.path.read_bytes() == external
    assert not list(tmp_path.glob('.settings-*'))


def test_save_revokes_pending_and_inflight_watcher_candidate(tmp_path, monkeypatch):
    service, config = make_service(tmp_path)
    service.path.write_bytes(service.path.read_bytes().replace(b'save_audio = False', b'save_audio = True'))
    service.reloader.poll()
    started, release = Event(), Event()
    prepare = service.reloader.prepare

    def paused_prepare(source):
        result = prepare(source)
        started.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(service.reloader, 'prepare', paused_prepare)
    with ThreadPoolExecutor(1) as pool:
        job = pool.submit(service.reloader.poll)
        assert started.wait(timeout=3)
        monkeypatch.setattr(service.reloader, 'prepare', prepare)
        try:
            service.save({'save_audio': False, 'ui_language': 'en'}, revision=service.read().revision)
        finally:
            release.set()
        job.result(timeout=3)
    assert service.reloader.apply() == ()
    assert config.save_audio is False
    assert publish(service) == ('ui_language',)


def test_save_replaces_pending_candidate_without_publishing(tmp_path):
    service, config = make_service(tmp_path)
    service.save({'save_audio': True}, revision=service.read().revision)
    service.reloader.poll()
    service.reloader.poll()
    assert service.reloader.pending == {'save_audio': True}
    service.save({'save_audio': False}, revision=service.read().revision)
    assert not service.reloader.apply()
    assert config.save_audio is False


def test_save_keeps_comments_bom_crlf_and_credentials_expression(tmp_path):
    path = tmp_path / 'config_client.py'
    original = (
        "\ufeffimport os\r\nclass ClientConfig:\r\n"
        "    auth_token = os.environ.get('CAPSWRITER_TEST_UNUSED_TOKEN', '')  # keep expression\r\n"
        "    save_audio: bool = False  # keep comment\r\n"
    ).encode('utf-8')
    path.write_bytes(original)
    service = SettingsService.standalone(path)
    snapshot = service.read()
    service.save({'save_audio': True}, revision=snapshot.revision)
    assert path.read_bytes() == original.replace(b'False', b'True')
    assert snapshot.effective is None and snapshot.pending is None


def test_private_values_are_not_in_snapshot_repr_or_errors(tmp_path):
    service, _ = make_service(tmp_path)
    service.save({'auth_token': 'synthetic-private-token'}, revision=service.read().revision)
    snapshot = service.read()
    assert snapshot.saved['ClientConfig']['auth_token'] == 'synthetic-private-token'
    assert 'synthetic-private-token' not in repr(snapshot)


def test_gui_save_respects_real_client_busy_boundary(tmp_path, monkeypatch):
    from core.client.app import CapsWriterClient
    from core.client.operations import ClientOperations
    from core.client.state import ClientState

    service, config = make_service(tmp_path)
    monkeypatch.setattr('core.client.app.Config', config)
    app = CapsWriterClient.__new__(CapsWriterClient)
    app.config_reload = service.reloader
    app.state = ClientState()
    app._stopping = app._file_active = False
    app._report_config = Mock()
    app.operations = ClientOperations(app)
    snapshot = service.read()
    app.state.dictation_uploads['synthetic-task'] = asyncio.Event()
    asyncio.run(app.operations.save_settings({'save_audio': True}, revision=snapshot.revision))
    service.reloader.poll()
    service.reloader.poll()
    app.apply_config_reload()
    assert config.save_audio is False
    app.state.dictation_uploads.clear()
    app.apply_config_reload()
    assert config.save_audio is True


def test_rapid_actions_edit_saved_state_and_serialize_with_language(tmp_path):
    from core.client.operations import ClientOperations

    service, config = make_service(tmp_path)
    operations = ClientOperations(SimpleNamespace(config_reload=service.reloader))

    async def run():
        await asyncio.gather(operations.toggle_llm('correct_asr'), operations.set_language('en'))
        saved = await operations.read_settings()
        assert saved.saved['ClientConfig']['llm_enabled'] is True
        assert saved.saved['ClientConfig']['ui_language'] == 'en'
        assert config.llm_enabled is False
        await operations.toggle_llm('correct_asr')
        assert (await operations.read_settings()).saved['ClientConfig']['llm_enabled'] is False

    asyncio.run(run())
    assert publish(service) == ('llm_correction_enabled', 'llm_translation_enabled', 'ui_language')


def test_close_rejects_save_and_submit_without_late_publication(tmp_path):
    from core.client.operations import ClientOperations

    service, config = make_service(tmp_path)
    loop = asyncio.new_event_loop()
    loop.close()
    operations = ClientOperations(SimpleNamespace(config_reload=service.reloader, loop=loop))
    coroutine = operations.toggle_llm()
    assert operations.submit(coroutine) is None
    assert coroutine.cr_frame is None
    revision = service.read().revision
    original = service.path.read_bytes()
    asyncio.run(service.reloader.close())
    with pytest.raises(CandidateError, match='stopping'):
        service.save({'save_audio': True}, revision=revision)
    assert service.path.read_bytes() == original
    assert not service.reloader.apply()
    assert config.save_audio is False


def test_application_actions_delegate_to_existing_resource_owners(tmp_path):
    from core.client.operations import ClientOperations

    service, _ = make_service(tmp_path)
    app = SimpleNamespace(
        config_reload=service.reloader, _stopping=False,
        state=SimpleNamespace(recording=True, dictation_paused=False),
        stream=SimpleNamespace(reopen=Mock()), toggle_dictation_pause=Mock(return_value=True),
    )
    operations = ClientOperations(app)

    async def run():
        assert await operations.reconnect_microphone()
        app.stream.reopen.assert_not_called()
        app.state.recording = False
        app.state.dictation_paused = True
        assert await operations.reconnect_microphone() is None
        app.stream.reopen.assert_not_called()
        app.state.dictation_paused = False
        await operations.reconnect_microphone()
        app.stream.reopen.assert_called_once()
        assert await operations.toggle_pause() is True
        app.toggle_dictation_pause.assert_called_once()
        app._stopping = True
        with pytest.raises(CandidateError):
            await operations.reconnect_microphone()
        with pytest.raises(CandidateError):
            await operations.toggle_pause()
        app.stream.reopen.assert_called_once()

    asyncio.run(run())


def test_duplicate_settings_class_is_rejected(tmp_path):
    path = tmp_path / 'config_client.py'
    path.write_text('class ClientConfig:\n    save_audio = True\nclass ClientConfig:\n    save_audio = False\n')
    snapshot = SettingsService.standalone(path).read()
    assert snapshot.saved is None
    assert 'Duplicate' in snapshot.error


def test_compound_value_comments_are_never_silently_deleted(tmp_path):
    path = tmp_path / 'config_client.py'
    original = b'class ClientConfig:\n    paste_apps = [\n        "fixture", # Keep per-app explanation\n    ]\n'
    path.write_bytes(original)
    service = SettingsService.standalone(path)
    revision = service.read().revision
    with pytest.raises(CandidateError, match='internal comments'):
        service.save({'paste_apps': ['other']}, revision=revision)
    assert path.read_bytes() == original
    # An unchanged literal does not require rewriting or losing internal comments.
    service.save({'paste_apps': ['fixture']}, revision=revision)
    assert path.read_bytes() == original
