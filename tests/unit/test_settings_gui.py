"""Settings desktop transactions and widgets use synthetic files, never live devices."""

from io import BytesIO
import json
import os
from pathlib import Path
import time

import pytest

from core.settings import SettingsConflict
from core.settings_gui.backend import Backend, recent_diagnostics
from core.settings_gui.bridge import LIMIT, PipeBackend, receive, send
from core.settings_gui.catalog import CatalogEditor


@pytest.fixture
def gui_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('core.settings_gui.device_watch.subscribe', lambda notify: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr('core.settings_gui.devices.discover_inputs', lambda: {
        'devices': [{'index': 2, 'name': 'USB Microphone', 'api': 'Windows WASAPI',
                     'selector': 'USB Microphone, Windows WASAPI', 'ambiguous': False}],
        'default': 2, 'partial': False, 'error': None,
    })
    root = Path(__file__).resolve().parents[2]
    (tmp_path / 'config_client.py').write_bytes((root / 'config_templates/config_client_template.py').read_bytes())
    directory = tmp_path / 'LLM'
    directory.mkdir()
    for name in ('providers.template.toml', 'presets.toml'):
        (directory / name).write_bytes((root / 'LLM' / name).read_bytes())
    return tmp_path


def test_catalog_keeps_credentials_comments_and_unknown_fields(gui_root):
    directory = gui_root / 'LLM'
    source = (directory / 'providers.template.toml').read_text(encoding='utf-8')
    source = source.replace('[providers.gemini]', "# keep this\n[providers.gemini]\ncustom_option = 'keep'")
    source = source.replace('api_key = ""', 'api_key = "private-test-key"')
    path = directory / 'providers.toml'
    path.write_text(source, encoding='utf-8')
    editor = CatalogEditor(directory)
    snapshot = editor.read()
    assert 'private-test-key' not in repr(snapshot)
    assert snapshot['providers']['gemini']['has_api_key']
    result = editor.save('providers', 'gemini', {'model': 'synthetic-model'}, revision=snapshot['revision'])
    saved = path.read_text(encoding='utf-8')
    assert '# keep this' in saved and "custom_option = 'keep'" in saved and 'private-test-key' in saved
    assert result['providers']['gemini']['model'] == 'synthetic-model'
    editor.save('providers', 'gemini', {'api_key': ''}, revision=result['revision'])
    assert 'private-test-key' not in path.read_text(encoding='utf-8')


def test_catalog_first_edit_conflicts_and_validation_are_atomic(gui_root):
    editor = CatalogEditor(gui_root / 'LLM')
    before = editor.read()
    assert not (editor.directory / 'providers.toml').exists()
    after = editor.save('providers', 'gemini', {'model': 'new-model'}, revision=before['revision'])
    assert (editor.directory / 'providers.toml').exists()
    original = (editor.directory / 'providers.toml').read_bytes()
    with pytest.raises(SettingsConflict):
        editor.save('providers', 'gemini', {'model': 'lost'}, revision=before['revision'])
    with pytest.raises(ValueError):
        editor.save('providers', 'gemini', {'timeout': 0}, revision=after['revision'])
    with pytest.raises(ValueError):
        editor.save('providers', 'gemini', {}, revision=after['revision'], delete=True)
    assert (editor.directory / 'providers.toml').read_bytes() == original


def test_preset_create_delete_switch_mode_and_default_reference(gui_root):
    editor = CatalogEditor(gui_root / 'LLM')
    snapshot = editor.read()
    after = editor.save('presets', 'example', {'name': 'Example', 'provider': 'gemini',
                        'system_prompt': 'Custom prompt', 'triggers': ['example']}, revision=snapshot['revision'])
    after = editor.save('presets', 'example', {'prompt_mode': 'correction'}, revision=after['revision'])
    assert after['presets']['example']['system_prompt'] == ''
    with pytest.raises(ValueError):
        editor.save('presets', 'example', {}, revision=after['revision'], delete=True, default='example')
    after = editor.save('presets', 'example', {}, revision=after['revision'], delete=True)
    assert 'example' not in after['presets']


def test_external_template_edit_is_detected_before_first_local_write(gui_root):
    editor = CatalogEditor(gui_root / 'LLM')
    snapshot = editor.read()
    path = editor.directory / 'providers.template.toml'
    path.write_bytes(path.read_bytes() + b'\n# external\n')
    with pytest.raises(SettingsConflict):
        editor.save('providers', 'gemini', {'model': 'lost'}, revision=snapshot['revision'])
    assert not (editor.directory / 'providers.toml').exists()


def test_atomic_catalog_failure_leaves_original_and_no_temp_files(gui_root, monkeypatch):
    editor = CatalogEditor(gui_root / 'LLM')
    snapshot = editor.read()
    def fail(*_):
        raise OSError('private path')
    monkeypatch.setattr('core.settings_gui.catalog.os.replace', fail)
    with pytest.raises(OSError):
        editor.save('providers', 'gemini', {'model': 'lost'}, revision=snapshot['revision'])
    assert editor.read() == snapshot
    assert not list(editor.directory.glob('.settings-*'))


def test_backend_preview_uses_unsaved_options_without_writing_or_network(gui_root, monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *_a, **_k: pytest.fail('network access'))
    backend = Backend(gui_root)
    original = (gui_root / 'config_client.py').read_bytes()
    snapshot = backend.dispatch('read', {})
    assert snapshot['effective'] is None
    assert snapshot['saved']['ClientConfig']['auth_token'] == '<redacted>'
    catalog = backend.dispatch('catalog', {})
    preview = backend.dispatch('preview', {'revision': snapshot['revision'],
        'changes': {'llm_correction_level': 'fluent', 'caret_context_enabled': True},
        'draft': {'kind': 'presets', 'identifier': 'correct_asr', 'changes': {'use_caret_context': False},
                  'revision': catalog['revision']}})
    assert not preview['context_allowed']
    assert preview['system_prompt']
    assert (gui_root / 'config_client.py').read_bytes() == original
    with pytest.raises(ValueError):
        backend.dispatch('save', {'revision': snapshot['revision'], 'changes': {'llm_default_preset': 'missing'}})
    assert not (gui_root / 'LLM/providers.toml').exists()
    assert backend.dispatch('costs', {'month': '2026-09'})['requests'] == 0


def test_recent_diagnostics_are_bounded_and_exclude_content(gui_root):
    path = gui_root / 'logs/client/2026/09/client-synthetic.jsonl'
    path.parent.mkdir(parents=True)
    lines = [json.dumps({'timestamp': f'{i:04}', 'data': {'http_status': 402},
                         'content': {'error_detail': 'private text'}}) for i in range(100)]
    path.write_text('\n'.join(lines) + '\n{partial', encoding='utf-8')
    records = recent_diagnostics(gui_root, {'diagnostic_log_dir': 'logs'})
    assert len(records) == 80 and records[-1]['timestamp'] == '0099'
    assert 'private text' not in repr(records)
    assert records[0]['data']['http_status'] == 402


@pytest.mark.parametrize('payload', [b'[]\n', b'{}', b'x\n', b'a' * (LIMIT + 1)],
                         ids=['array', 'unterminated', 'invalid-json', 'oversized'])
def test_bridge_rejects_malformed_or_oversized_frames(payload):
    with pytest.raises(ValueError):
        receive(BytesIO(payload))


def test_bridge_eof_roundtrip_and_request_identity():
    with pytest.raises(EOFError):
        receive(BytesIO())
    stream = BytesIO()
    send(stream, {'id': 1, 'result': {'saved': True}})
    stream.seek(0)
    output = BytesIO()
    backend = PipeBackend(stream, output)
    assert backend.dispatch('read', {}) == {'saved': True}
    assert json.loads(output.getvalue())['id'] == 1
    with pytest.raises(ValueError):
        PipeBackend(BytesIO(b'{"id":9,"result":{}}\n'), BytesIO()).dispatch('read', {})


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def settle(app, window):
    deadline = time.monotonic() + 5
    def pending():
        return (window.busy or window.history.inflight or window.history.pending is not None
                or window.history.auto_search.isActive() or window.autosave.isActive()
                or window.device_retry.isActive() or window.devices_inflight)
    while pending() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not pending()


@pytest.fixture
def window(gui_root, qt_app):
    from core.settings_gui.window import SettingsWindow
    widget = SettingsWindow(Backend(gui_root))
    widget.poll.stop()
    widget.show()
    settle(qt_app, widget)
    yield widget
    settle(qt_app, widget)
    widget.confirm_discard = lambda: True
    widget.close()
    qt_app.processEvents()
    assert not widget.thread.is_alive()


def test_widgets_save_keyboard_and_no_unintended_writes(window, qt_app, gui_root):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    assert not window.changes()
    assert window.catalog and window.navigation.count() == 6
    window.navigation.setFocus()
    QTest.keyClick(window.navigation, Qt.Key.Key_Down)
    assert window.pages.currentIndex() == 1
    window.fields['llm_correction_english'][0].setChecked(True)
    assert window.changes() == {'llm_correction_english': True}
    assert not (gui_root / 'LLM/providers.toml').exists()
    window.save()
    settle(qt_app, window)
    assert not window.changes()
    assert Backend(gui_root).config()['llm_correction_english']
    assert Backend(gui_root).config()['llm_default_preset'] == 'correct_asr'
    assert 'llm_default_preset' not in window.fields
    assert not hasattr(window, 'preview_button')
    assert all(widget.accessibleName() for widget, _ in window.fields.values())


def test_widgets_conflict_preserves_draft_and_failed_save_is_visible(window, qt_app, gui_root, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    window.fields['save_audio'][0].setChecked(True)
    path = gui_root / 'config_client.py'
    path.write_bytes(path.read_bytes() + b'\n# external edit\n')
    window.save()
    settle(qt_app, window)
    assert not warnings and window.save_error and window.changes()['save_audio']
    assert not window.retry_save.isHidden()
    assert not Backend(gui_root).config()['save_audio']
    assert 'external edit' in path.read_text(encoding='utf-8')


def test_provider_draft_survives_master_switch_autosave(window, qt_app):
    fields = window.editors['presets'][2]
    window.fields['llm_enabled'][0].setChecked(True)
    fields['provider'][0].setCurrentIndex(fields['provider'][0].findData('local'))
    settle(qt_app, window)
    assert window.entry_dirty('presets')
    assert fields['provider'][0].currentData() == 'local'
    provider_choice = window.editors['presets'][2]['provider'][0]
    provider_choice.activated.emit(provider_choice.currentIndex())
    settle(qt_app, window)
    assert not window.entry_dirty('presets')
    assert window.catalog['presets']['correct_asr']['provider'] == 'local'
    assert window.provider_info['model'].text() == window.catalog['providers']['local']['model']
    assert window.catalog['presets']['translate']['provider'] == 'gemini'


def test_advanced_routing_and_numeric_device_are_preserved(window, qt_app, gui_root):
    from core.settings import SettingsService
    service = SettingsService.standalone(gui_root / 'config_client.py')
    service.save({'llm_default_preset': None, 'llm_correction_enabled': False}, revision=service.read().revision)
    window.reload()
    settle(qt_app, window)
    assert not window.cleanup_notice.isHidden()
    window.fields['llm_correction_english'][0].setChecked(True)
    settle(qt_app, window)
    assert service.read().saved['ClientConfig']['llm_default_preset'] is None
    assert service.read().saved['ClientConfig']['llm_correction_enabled'] is False
    window.snapshot['saved']['ClientConfig']['input_device'] = 3
    window.fields['input_device'][0].set_config_value(3)
    window.form_baseline['input_device'] = 3
    assert 'input_device' not in window.changes()


def test_language_and_microphone_choices_save_canonical_values(window, qt_app, gui_root):
    language = window.fields['language'][0]
    microphone = window.fields['input_device'][0]
    assert not language.isEditable() and not microphone.isEditable()
    window.navigate(0)
    settle(qt_app, window)
    assert window.devices_loaded_once and not window.changes()
    language.setCurrentIndex(language.findData('english'))
    microphone.setCurrentIndex(microphone.findData('USB Microphone, Windows WASAPI'))
    settle(qt_app, window)
    config = Backend(gui_root).config()
    assert config['language'] == 'english'
    assert config['input_device'] == 'USB Microphone, Windows WASAPI'
    microphone.setCurrentIndex(0)
    settle(qt_app, window)
    assert Backend(gui_root).config()['input_device'] is None


def test_device_refresh_preserves_missing_selection_and_never_writes(window, qt_app, gui_root, monkeypatch):
    microphone = window.fields['input_device'][0]
    microphone.set_config_value('Saved disconnected microphone')
    settle(qt_app, window)
    before = (gui_root / 'config_client.py').read_bytes()
    window.navigate(0)
    settle(qt_app, window)
    assert microphone.currentData() == 'Saved disconnected microphone'
    assert not microphone.selection_found() and not window.device_notice.isHidden()
    assert (gui_root / 'config_client.py').read_bytes() == before
    monkeypatch.setattr('core.settings_gui.devices.discover_inputs', lambda: {
        'devices': [], 'default': -1, 'partial': False, 'error': 'timeout',
    })
    window.refresh_devices()
    settle(qt_app, window)
    assert microphone.currentData() == 'Saved disconnected microphone'
    assert 'timed out' in window.device_notice.accessibleDescription()
    assert (gui_root / 'config_client.py').read_bytes() == before


@pytest.mark.parametrize('width', [800, 1440])
def test_microphone_initial_query_failure_and_recovery_keep_field_geometry(window, qt_app, monkeypatch, width):
    from threading import Event
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QToolTip
    from core.settings_gui.fields import PAGES
    entered, release = Event(), Event()
    inventory = {'devices': [{'index': 1, 'name': 'Synthetic microphone', 'api': 'Windows WASAPI',
                             'selector': 'Synthetic microphone, Windows WASAPI', 'ambiguous': False}],
                 'default': 1, 'partial': False, 'error': None}
    def discover():
        entered.set()
        assert release.wait(3)
        return inventory
    monkeypatch.setattr('core.settings_gui.devices.discover_inputs', discover)
    window.resize(width, 860)
    window.pages.setCurrentIndex(0)
    window.workspace.setCurrentIndex(1)
    def geometry():
        for _ in range(5):
            qt_app.processEvents()
        return [(widget.mapTo(window, QPoint()), widget.size())
                for name in PAGES['general'] for widget, _ in [window.fields[name]]]
    baseline = geometry()
    window.refresh_devices()
    assert entered.wait(2)
    try:
        assert window.device_notice.isHidden()
        assert geometry() == baseline
    finally:
        release.set()
    settle(qt_app, window)
    assert window.device_notice.isHidden() and geometry() == baseline
    window.devices_loaded({'devices': [], 'default': -1, 'partial': False, 'error': 'timeout'})
    assert not window.device_notice.isHidden() and geometry() == baseline
    shown = []
    monkeypatch.setattr(QToolTip, 'showText', lambda *args: shown.append(args[1]))
    window.device_notice.click()
    assert 'timed out' in shown[-1]
    window.refresh_devices()
    assert not window.device_notice.isHidden() and geometry() == baseline
    settle(qt_app, window)
    assert window.device_notice.isHidden() and geometry() == baseline


def test_device_refresh_retains_edits_made_while_loading(window, qt_app, gui_root, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    def discover():
        entered.set()
        assert release.wait(3)
        return {'devices': [], 'default': -1, 'partial': False, 'error': None}
    monkeypatch.setattr('core.settings_gui.devices.discover_inputs', discover)
    window.navigate(0)
    assert entered.wait(2)
    try:
        assert window.fields['input_device'][0].isEnabled()
        window.fields['language'][0].set_config_value('english')
        window.fields['input_device'][0].set_config_value('Unlisted custom selector')
    finally:
        release.set()
    settle(qt_app, window)
    config = Backend(gui_root).config()
    assert config['language'] == 'english' and config['input_device'] == 'Unlisted custom selector'


@pytest.mark.parametrize('saved', [None, '', 2, 'USB Microphone', 'custom substring'])
def test_device_refresh_preserves_legacy_value_types(window, qt_app, gui_root, saved):
    backend = Backend(gui_root)
    snapshot = backend.dispatch('read', {})
    backend.dispatch('save', {'revision': snapshot['revision'], 'changes': {'input_device': saved}})
    window.populate_settings(backend.dispatch('read', {}))
    microphone = window.fields['input_device'][0]
    window.refresh_devices()
    settle(qt_app, window)
    assert type(microphone.currentData()) is type(saved)
    assert microphone.currentData() == saved and not window.changes()


def test_close_waits_for_device_probe_then_stops_worker(window, qt_app, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    def discover():
        entered.set()
        assert release.wait(3)
        return {'devices': [], 'default': -1, 'partial': False, 'error': None}
    monkeypatch.setattr('core.settings_gui.devices.discover_inputs', discover)
    window.refresh_devices()
    assert entered.wait(2)
    try:
        window.close()
        assert window.close_pending and not window.closed.is_set()
    finally:
        release.set()
    settle(qt_app, window)
    assert window.closed.is_set() and not window.thread.is_alive()
    assert not window.device_retry.isActive()


def test_unknown_language_is_preserved_when_other_fields_save(window, qt_app, gui_root):
    language = window.fields['language'][0]
    language.set_config_value('custom-language')
    settle(qt_app, window)
    window.fields['save_audio'][0].setChecked(True)
    settle(qt_app, window)
    assert language.currentData() == 'custom-language'
    assert Backend(gui_root).config()['language'] == 'custom-language'


def test_ambiguous_device_names_are_disabled_but_legacy_indices_survive(qt_app):
    from core.settings_gui.choices import MicrophoneChoice
    microphone = MicrophoneChoice()
    rows = [{'index': index, 'name': name, 'api': 'MME', 'selector': name + ', MME', 'ambiguous': True}
            for index, name in enumerate(('Same mic', 'same MIC'))]
    microphone.replace_inventory({'devices': rows, 'default': 0, 'partial': False, 'error': None})
    assert all(not microphone.model().item(index).isEnabled() for index in (1, 2))
    microphone.set_config_value('Same mic, MME')
    assert not microphone.selection_found()
    microphone.set_config_value(0)
    assert microphone.selection_found() and type(microphone.currentData()) is int


def test_device_notifications_coalesce_defer_when_hidden_and_stop(window, qt_app, monkeypatch):
    from types import SimpleNamespace
    clock = [10.0]
    calls, closed = [], []
    watch = window.device_watch
    monkeypatch.setattr('core.settings_gui.device_watch.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('core.settings_gui.device_watch.subscribe',
                        lambda notify: SimpleNamespace(close=lambda: closed.append(True)))
    watch.visible = lambda: window.isVisible()
    watch.refresh = lambda: calls.append(True)
    watch.start()
    for _ in range(50):
        watch.notify()
    assert len(watch.events) == 1
    watch.tick()
    assert not calls
    clock[0] += 1
    window.hide()
    watch.tick()
    assert not calls and watch.dirty
    window.show()
    watch.tick()
    assert calls == [True] and not watch.dirty
    watch.tick()
    assert calls == [True]
    watch.stop()
    watch.notify()
    watch.tick()
    assert closed == [True] and not watch.events and not watch.timer.isActive()


def test_device_watch_uses_slow_visible_fallback_if_subscription_fails(window, monkeypatch):
    clock, calls = [10.0], []
    watch = window.device_watch
    monkeypatch.setattr('core.settings_gui.device_watch.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('core.settings_gui.device_watch.subscribe', lambda notify: None)
    watch.visible = lambda: True
    watch.refresh = lambda: calls.append(True)
    watch.start()
    clock[0] += 14
    watch.tick()
    assert not calls
    clock[0] += 1
    watch.tick()
    assert calls == [True]
    watch.tick()
    assert calls == [True]
    watch.stop()


def test_device_unsubscribe_failure_keeps_callback_alive_but_ignores_late_events(window):
    from types import SimpleNamespace
    calls = []
    def close():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('Synthetic native failure')
    watch = window.device_watch
    watch.native = SimpleNamespace(close=close)
    watch.started = True
    watch.stop()
    assert watch.native is not None and not watch.started
    watch.notify()
    assert not watch.events
    watch.stop()
    assert watch.native is None and len(calls) == 2


def test_device_event_during_probe_runs_one_followup_query(window, qt_app, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    calls = []
    def discover():
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return {'devices': [], 'default': -1, 'partial': False, 'error': None}
    monkeypatch.setattr('core.settings_gui.devices.discover_inputs', discover)
    window.refresh_devices()
    assert entered.wait(2)
    try:
        for _ in range(10):
            window.refresh_devices()
        assert window.devices_pending
    finally:
        release.set()
    settle(qt_app, window)
    assert len(calls) == 2 and not window.devices_pending


def test_device_refresh_defers_open_menu_and_unchanged_inventory_keeps_model(window, qt_app):
    microphone = window.fields['input_device'][0]
    window.navigate(0)
    settle(qt_app, window)
    item = microphone.model().item(1)
    microphone.showPopup()
    qt_app.processEvents()
    window.refresh_devices()
    assert not window.devices_inflight and window.device_retry.isActive()
    assert microphone.model().item(1) is item
    microphone.hidePopup()
    settle(qt_app, window)
    assert microphone.model().item(1) is item


def test_microphone_menu_hides_interfaces_preserves_old_selection_and_default_updates(qt_app):
    from core.settings_gui.choices import MicrophoneChoice
    microphone = MicrophoneChoice()
    rows = [{'index': i, 'name': name, 'api': api, 'selector': name + ', ' + api, 'ambiguous': False}
            for i, (name, api) in enumerate([('Headset', 'Windows WASAPI'), ('Laptop', 'Windows WASAPI'),
                                            ('Headset', 'MME'), ('Laptop', 'Windows WDM-KS')])]
    microphone.replace_inventory({'devices': rows, 'default': 2, 'partial': False, 'error': None})
    assert microphone.count() == 3
    assert [microphone.itemText(i) for i in (1, 2)] == ['Headset', 'Laptop']
    assert 'Headset' in microphone.itemText(0)
    microphone.set_config_value('Headset, MME')
    microphone.replace_inventory({'devices': rows, 'default': 1, 'partial': False, 'error': None})
    assert microphone.currentData() == 'Headset, MME' and microphone.selection_found()
    assert 'Laptop' in microphone.itemText(0)


def test_opening_preserves_numeric_types_empty_nullables_and_fractional_settings(gui_root, qt_app):
    from core.settings import SettingsService
    from core.settings_gui.window import SettingsWindow
    service = SettingsService.standalone(gui_root / 'config_client.py')
    service.save({'port': 6016, 'input_device': '', 'mic_seg_duration': 60.125,
                  'idle_suspend_seconds': 10.012345678}, revision=service.read().revision)
    window = SettingsWindow(Backend(gui_root))
    window.poll.stop()
    settle(qt_app, window)
    assert not window.changes()
    window.fields['save_audio'][0].setChecked(True)
    window.save()
    settle(qt_app, window)
    values = service.read().saved['ClientConfig']
    assert type(values['port']) is int and values['input_device'] == ''
    assert values['mic_seg_duration'] == 60.125 and values['idle_suspend_seconds'] == 10.012345678
    window.close()


@pytest.fixture
def attached(gui_root, attach_client_operations):
    import asyncio
    from threading import Thread
    from types import SimpleNamespace
    from core.settings import SettingsService
    values = SettingsService.standalone(gui_root / 'config_client.py').read().saved['ClientConfig']
    loop = asyncio.new_event_loop()
    app = SimpleNamespace(base_dir=gui_root, loop=loop, _stopping=False, _file_active=False,
                          state=SimpleNamespace(is_connected=True, recording=False, dictation_paused=False))
    config = SimpleNamespace(**values)
    operations = attach_client_operations(app, config)
    thread = Thread(target=loop.run_forever)
    thread.start()
    yield app, Backend(gui_root, operations), config
    asyncio.run_coroutine_threadsafe(app.config_reload.close(), loop).result(timeout=5)
    asyncio.run_coroutine_threadsafe(loop.shutdown_default_executor(), loop).result(timeout=5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def test_attached_settings_save_pending_restart_and_publication(attached):
    app, backend, config = attached
    snapshot = backend.dispatch('read', {})
    assert snapshot['runtime']['connected']
    result = backend.dispatch('save', {'revision': snapshot['revision'],
                                      'changes': {'save_audio': True, 'enable_tray': False}})
    assert result['pending'] == ['ClientConfig.save_audio']
    assert result['restart_required'] == ['ClientConfig.enable_tray']
    assert not config.save_audio and config.enable_tray
    app.config_reload.poll()
    app.config_reload.poll()
    app.config_reload.apply()
    updated = backend.dispatch('read', {})
    assert updated['pending'] == []
    assert updated['restart_required'] == ['ClientConfig.enable_tray']
    app._stopping = True
    with pytest.raises(ValueError):
        backend.dispatch('save', {'revision': result['revision'], 'changes': {'save_audio': False}})


def test_owned_gui_process_reuses_window_and_reaps_only_its_child(attached, monkeypatch):
    from threading import Event
    from core.settings_gui.bridge import SettingsProcess
    app, backend, _ = attached
    root = Path(__file__).resolve().parents[2]
    (app.base_dir / 'start_client.py').write_bytes((root / 'start_client.py').read_bytes())
    monkeypatch.setenv('PYTHONPATH', str(root))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    controller = SettingsProcess(app.base_dir, app.operations)
    served = Event()
    original = backend.dispatch
    def dispatch(method, params):
        result = original(method, params)
        if method == 'catalog':
            served.set()
        return result
    backend.dispatch = dispatch
    controller.backend = backend
    try:
        controller.open()
        process = controller.process
        assert served.wait(timeout=10)
        assert process.poll() is None
        controller.open()
        assert controller.process is process
    finally:
        controller.stop()
    assert process.poll() is not None and not controller.thread.is_alive()
    controller.open()
    assert controller.process is process


def test_parent_pipe_eof_closes_gui_without_a_modal_prompt(qt_app):
    from core.settings_gui.window import SettingsWindow
    widget = SettingsWindow(PipeBackend(BytesIO(), BytesIO()))
    widget.show()
    settle(qt_app, widget)
    assert widget.closed.is_set() and not widget.isVisible()


def test_gui_cli_dispatch_does_not_start_dictation(gui_root, monkeypatch):
    from core.settings_cli import main
    calls = []
    monkeypatch.setattr('core.settings_gui.main.main', lambda root: calls.append(root) or 0)
    assert main(['gui'], root=gui_root) == 0
    assert calls == [gui_root]


def test_desktop_autostarts_client_once_and_reports_failure_without_starting_server(gui_root):
    from core.settings_gui.desktop import DesktopBackend
    from unittest.mock import Mock
    saved = Backend(gui_root).dispatch('read', {})
    session = Mock()
    session.call.return_value = {**saved, 'runtime': {'connected': False, 'recording': False}}
    factory = Mock(return_value=session)
    backend = DesktopBackend(gui_root, factory)
    first = backend.dispatch('read', {})
    assert first['desktop']['phase'] == 'running'
    backend.dispatch('read', {})
    session.start.assert_called_once_with()
    session.call.side_effect = RuntimeError('private failure details')
    failed = backend.dispatch('read', {})
    assert failed['desktop']['phase'] == 'failed' and failed['runtime'] is None
    assert 'private failure details' not in str(failed['desktop'])
    session.close.assert_called_once_with(graceful=False)
    factory.assert_called_once_with(gui_root)


def test_desktop_boot_failure_keeps_configuration_accessible(gui_root):
    from core.settings_gui.desktop import DesktopBackend
    from unittest.mock import Mock
    session = Mock()
    session.start.side_effect = OSError('private path')
    backend = DesktopBackend(gui_root, lambda _: session)
    assert backend.dispatch('read', {})['desktop']['phase'] == 'failed'
    assert backend.dispatch('catalog', {})['presets']
    session.start.side_effect = None
    backend.dispatch('desktop_start', {})
    assert session.start.call_count == 2
    backend.dispatch('desktop_stop', {})
    assert backend.phase == 'stopped' and backend.session is None


@pytest.mark.parametrize('stubborn', [False, True])
def test_desktop_session_boot_pipe_and_owned_shutdown(gui_root, stubborn):
    from core.settings_gui.desktop import ClientSession
    script = '''
import json, sys, time
for line in sys.stdin.buffer:
    request = json.loads(line)
    result = {'runtime': {'connected': True}} if request['method'] == 'read' else True
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
    if request['method'] == 'shutdown':
        break
'''
    (gui_root / 'start_client.py').write_text(script, encoding='utf-8')
    session = ClientSession(gui_root)
    session.start()
    assert session.call('read', {})['runtime']['connected']
    process = session.process
    session.close(graceful=not stubborn)
    assert process.poll() is not None
    if not stubborn:
        assert process.returncode == 0
    assert not session.reader.is_alive()


def test_desktop_presence_reactivates_one_instance(gui_root, qt_app):
    from core.settings_gui.shell import DesktopPresence
    from unittest.mock import Mock
    first = DesktopPresence(gui_root)
    second = DesktopPresence(gui_root)
    window = Mock()
    try:
        assert first.acquire()
        first.bind(window)
        assert not second.acquire()
        for _ in range(5):
            qt_app.processEvents()
        window.showNormal.assert_called()
    finally:
        first.close()
        second.close()


def test_desktop_closing_window_hides_without_stopping_client(window, qt_app):
    from unittest.mock import Mock
    window.desktop_mode = True
    window.tray = Mock()
    window.tray.isVisible.return_value = True
    window.close()
    qt_app.processEvents()
    assert not window.isVisible() and not window.closed.is_set()
    window.desktop_mode = False


def test_modern_switch_keeps_keyboard_semantics(window, qt_app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window.navigate(1)
    toggle = window.fields['llm_enabled'][0]
    before = toggle.isChecked()
    toggle.setFocus()
    QTest.keyClick(toggle, Qt.Key.Key_Space)
    assert toggle.isChecked() is not before
    assert toggle.accessibleName()


def test_desktop_entry_dispatch_is_separate_from_console_default(monkeypatch):
    import start_client
    import runpy
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock
    desktop = Mock(return_value=0)
    client = Mock()
    client.return_value.start.return_value = 0
    parser = Mock(return_value='synthetic mic command')
    monkeypatch.setitem(sys.modules, 'core.client.cli', SimpleNamespace(parse_client_command=parser))
    monkeypatch.setitem(sys.modules, 'core.client.app', SimpleNamespace(CapsWriterClient=client))
    monkeypatch.setattr('core.settings_gui.main.main', desktop)
    assert start_client.main([]) == 0
    parser.assert_called_with([])
    assert start_client.main(['mic']) == 0
    parser.assert_called_with(['mic'])
    desktop.assert_not_called()
    assert client.call_count == 2
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'start_desktop.pyw'))
    assert namespace['main']() == 0
    desktop.assert_called_with(desktop=True)


def write_synthetic_client(gui_root, monkeypatch, outcome='normal'):
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[2]))
    script = '''
import asyncio, os, sys, time, types
from pathlib import Path
from core.settings import SettingsService
package = types.ModuleType('core.client')
package.__path__ = []
sys.modules['core.client'] = package
cli = types.ModuleType('core.client.cli')
cli.ClientMode = types.SimpleNamespace(MIC='mic')
cli.ClientCommand = lambda value: value
sys.modules['core.client.cli'] = cli
module = types.ModuleType('core.client.app')
class App:
    def __init__(self, command):
        self.stopped = False
        if EARLY_STOP:
            time.sleep(0.15)
        print('synthetic console output')
        os.write(1, b'synthetic native output\\n')
        if FAILURE:
            raise ValueError('private startup detail')
        self.loop = asyncio.new_event_loop()
        self.settings = SettingsService.standalone(Path.cwd() / 'config_client.py')
        self.operations = self
    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)
    async def read_settings(self):
        return self.settings.read()
    async def read_status(self):
        return {'connected': True, 'recording': False}
    def start(self):
        if ENDS_ITSELF or self.stopped:
            self.loop.close()
            return
        self.loop.run_forever()
        self.loop.close()
    def stop(self):
        self.stopped = True
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
module.CapsWriterClient = App
sys.modules['core.client.app'] = module
from core.settings_gui.worker import main
main()
'''.replace('FAILURE', str(outcome == 'failure')).replace('ENDS_ITSELF', str(outcome == 'returns')).replace(
        'EARLY_STOP', str(outcome == 'early_stop'))
    (gui_root / 'start_client.py').write_text(script, encoding='utf-8')


@pytest.mark.parametrize('outcome', ['normal', 'failure', 'returns', 'disconnect', 'early_stop'])
def test_hidden_worker_reports_startup_and_isolates_native_stdout(gui_root, monkeypatch, outcome):
    from core.settings_gui.desktop import ClientSession
    write_synthetic_client(gui_root, monkeypatch, outcome)
    session = ClientSession(gui_root)
    try:
        session.start()
        if outcome == 'early_stop':
            session.close()
        elif outcome == 'returns':
            # Output EOF must release stdin even without a desktop shutdown request.
            session.process.wait(timeout=5)
        else:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = session.call('read', {})
                if result.get('runtime') or result.get('worker_error'):
                    break
                time.sleep(0.01)
            assert bool(result.get('worker_error')) == (outcome == 'failure')
            assert 'private startup detail' not in str(result)
            assert outcome == 'failure' or result['runtime']['connected']
            if outcome == 'disconnect':
                session.process.stdin.close()
                session.process.wait(timeout=5)
    finally:
        session.close()
    assert session.process.returncode == 0


def test_tray_uses_microphone_icon_and_existing_window_actions(window, monkeypatch):
    from core.settings_gui.shell import install_tray
    from PySide6.QtWidgets import QSystemTrayIcon
    from unittest.mock import Mock
    factory = Mock()
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    monkeypatch.setattr(QSystemTrayIcon, 'show', factory)
    tray = install_tray(window, True)
    assert tray is not None and not tray.icon().isNull()
    assert len(tray.contextMenu().actions()) == 3
    factory.assert_called_once()
    window.hide()
    tray.contextMenu().actions()[0].trigger()
    assert window.isVisible()
    tray.hide()


@pytest.mark.parametrize('focused', [False, True])
@pytest.mark.parametrize('kind', [int, float, ('first', 'second')])
def test_wheel_over_controls_scrolls_page_without_changing_value(qt_app, focused, kind):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
    from core.settings_gui.window import get_value, make_input
    scroll = QScrollArea()
    scroll.resize(400, 300)
    content = QWidget()
    layout = QVBoxLayout(content)
    control = make_input('timeout', kind)
    layout.addWidget(control)
    layout.addSpacing(1200)
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    scroll.show()
    qt_app.processEvents()
    control.setFocus() if focused else control.clearFocus()
    before = get_value(control, kind)
    try:
        position = control.mapTo(scroll, control.rect().center())
        QTest.wheelEvent(scroll.windowHandle(), position, QPoint(0, -120))
        qt_app.processEvents()
        assert get_value(control, kind) == before
        assert scroll.verticalScrollBar().value() > 0
        scroll.verticalScrollBar().setValue(0)
        control.setFocus()
        QTest.keyClick(control, Qt.Key.Key_Down if isinstance(kind, tuple) else Qt.Key.Key_Up)
        assert get_value(control, kind) != before
    finally:
        scroll.close()


def test_background_refresh_keeps_buttons_stable_and_defers_save(window, qt_app, gui_root, monkeypatch):
    import threading
    from PySide6.QtCore import QEvent, QObject

    class EnabledChanges(QObject):
        changes = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.EnabledChange:
                self.changes += 1
            return False

    monitor = EnabledChanges()
    window.advanced.installEventFilter(monitor)
    original = window.backend.dispatch
    entered, release = threading.Event(), threading.Event()

    def slow_read(method, params):
        if method == 'read':
            entered.set()
            assert release.wait(3)
        return original(method, params)

    monkeypatch.setattr(window.backend, 'dispatch', slow_read)
    window.fields['save_audio'][0].setChecked(True)
    window.poll_state()
    assert entered.wait(2)
    try:
        assert window.advanced.isEnabled() and window.pages.isEnabled()
        assert monitor.changes == 0
        window.save()
        assert window.autosave.isActive()
    finally:
        release.set()
    settle(qt_app, window)
    assert Backend(gui_root).config()['save_audio']
    assert window.advanced.isEnabled() and not window.changes()
    assert monitor.changes == 0
    monitor.changes = 0
    window.next_settings_poll = 0
    window.poll_state()
    settle(qt_app, window)
    assert monitor.changes == 0


def test_external_settings_refresh_clean_form_but_preserve_drafts(window, qt_app, gui_root):
    from core.settings import SettingsService
    service = SettingsService.standalone(gui_root / 'config_client.py')
    assert window.resolve_button.isHidden()
    service.save({'save_audio': True}, revision=service.read().revision)
    window.poll_state()
    settle(qt_app, window)
    assert window.fields['save_audio'][0].isChecked() and not window.changes()
    assert window.resolve_button.isHidden()
    window.fields['save_audio'][0].setChecked(False)
    service.save({'port': 6020}, revision=service.read().revision)
    window.next_settings_poll = 0
    window.poll_state()
    settle(qt_app, window)
    assert window.changes() == {'save_audio': False}
    assert not window.resolve_button.isHidden()
    window.confirm_discard = lambda: False
    window.reload()
    assert window.changes() == {'save_audio': False}
    window.confirm_discard = lambda: True
    window.reload()
    settle(qt_app, window)
    assert not window.changes() and window.fields['port'][0].text() == '6020'
    assert window.resolve_button.isHidden()


def test_external_catalog_refresh_keeps_selection_and_protects_draft(window, qt_app, gui_root):
    editor = CatalogEditor(gui_root / 'LLM')
    fields = window.editors['presets'][2]
    editor.save('presets', 'correct_asr', {'provider': 'local'}, revision=editor.read()['revision'])
    window.poll_state()
    settle(qt_app, window)
    assert fields['provider'][0].currentData() == 'local'
    fields['provider'][0].setCurrentIndex(fields['provider'][0].findData('gemini'))
    editor.save('presets', 'correct_asr', {'temperature': 0.4}, revision=editor.read()['revision'])
    window.next_settings_poll = 0
    window.poll_state()
    settle(qt_app, window)
    assert fields['provider'][0].currentData() == 'gemini'
    assert window.catalog_conflict and not window.resolve_button.isHidden()


def test_recording_badge_tracks_runtime_and_clears_on_disconnect(window):
    from unittest.mock import Mock
    from PySide6.QtGui import QIcon
    root = Path(__file__).resolve().parents[2]
    original = QIcon(str(root / 'assets/client-icon.ico')).pixmap(64, 64).toImage()
    window.tray = Mock()
    window.icon_recording = None
    window.update_runtime({'connected': True, 'recording': False})
    assert window.windowIcon().pixmap(64, 64).toImage() == original
    window.update_runtime({'connected': True, 'recording': True})
    recorded = window.windowIcon().pixmap(64, 64).toImage()
    assert recorded != original
    center = round(52 * recorded.devicePixelRatio())
    assert recorded.pixelColor(center, center).red() == 255
    assert recorded.pixelColor(center, center).green() == 59
    window.update_runtime({'connected': True, 'recording': True})
    assert window.tray.setIcon.call_count == 2
    window.update_runtime(None)
    assert window.windowIcon().pixmap(64, 64).toImage() == original
    assert window.tray.setIcon.call_count == 3
    window.tray = None


def test_compact_settings_font_reaches_child_controls(window):
    assert window.fields['input_device'][0].font().pixelSize() == 12
    assert window.advanced.font().pixelSize() == 12


def test_autosave_coalesces_typing_and_keeps_newer_edits_during_write(window, qt_app, gui_root, monkeypatch):
    import threading
    dispatch = window.backend.dispatch
    entered, release = threading.Event(), threading.Event()
    writes = []

    def delayed(method, params):
        if method == 'save':
            writes.append(dict(params['changes']))
            if len(writes) == 1:
                entered.set()
                assert release.wait(3)
        return dispatch(method, params)

    monkeypatch.setattr(window.backend, 'dispatch', delayed)
    port = window.fields['port'][0]
    port.setText('60')
    port.setText('6020')
    window.save()
    assert entered.wait(2)
    try:
        assert window.pages.isEnabled()
        port.setText('6030')
        window.fields['save_audio'][0].setChecked(True)
    finally:
        release.set()
    settle(qt_app, window)
    assert writes == [{'port': '6020'}, {'port': '6030', 'save_audio': True}]
    assert Backend(gui_root).config()['port'] == '6030'
    assert not window.changes()


@pytest.mark.parametrize(('name', 'value'), [
    ('port', ''), ('port', '65536'), ('port', 'abc'), ('port', '9' * 5000), ('addr', 'ws://localhost'),
    ('language', ' '), ('idle_suspend_seconds', 0), ('mic_seg_duration', 0),
    ('mic_seg_overlap', 31), ('audio_name_len', 201), ('transcript_dir', ''),
])
def test_invalid_autosave_keeps_disk_unchanged_with_inline_feedback(window, qt_app, gui_root, name, value):
    from core.settings_gui.window import set_value
    path = gui_root / 'config_client.py'
    original = path.read_bytes()
    widget, kind = window.fields[name]
    set_value(widget, kind, value)
    settle(qt_app, window)
    assert name in window.input_errors
    assert not window.field_states[name].isHidden()
    assert path.read_bytes() == original
    assert window.changes()


def test_cross_field_validation_recovers_after_overlap_is_corrected(window, qt_app, gui_root):
    window.fields['mic_seg_duration'][0].setValue(2)
    window.fields['mic_seg_overlap'][0].setValue(3)
    settle(qt_app, window)
    assert 'mic_seg_overlap' in window.input_errors
    window.fields['mic_seg_overlap'][0].setValue(1)
    settle(qt_app, window)
    assert not window.input_errors and not window.changes()
    assert Backend(gui_root).config()['mic_seg_duration'] == 2


def test_save_failure_stays_visible_without_retry_loop_and_can_retry(window, qt_app, gui_root, monkeypatch):
    dispatch = window.backend.dispatch
    failures = []
    def fail(method, params):
        if method == 'save':
            failures.append(1)
            raise OSError('synthetic write failure')
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', fail)
    window.fields['save_audio'][0].setChecked(True)
    settle(qt_app, window)
    assert window.save_error and window.changes() and len(failures) == 1
    message = window.save_status.text()
    window.poll_state()
    settle(qt_app, window)
    assert window.save_status.text() == message and len(failures) == 1
    assert not Backend(gui_root).config()['save_audio']
    monkeypatch.setattr(window.backend, 'dispatch', dispatch)
    window.retry_save.click()
    settle(qt_app, window)
    assert Backend(gui_root).config()['save_audio'] and not window.changes()
    assert window.retry_save.isHidden()


def test_client_options_have_bilingual_help_and_language_restart_is_explicit(window, qt_app):
    from core.i18n import tr
    from core.settings_gui.window import label
    for name, (widget, _) in window.fields.items():
        assert widget.accessibleDescription()
        for locale in ('en', 'zh-CN'):
            assert tr('gui.help.' + name, locale=locale) != 'gui.help.' + name
    for _, identifier, widgets, _ in window.editors.values():
        if identifier is not None:
            assert identifier.accessibleDescription()
        for name, (widget, _) in widgets.items():
            assert widget.toolTip() and widget.accessibleDescription()
            assert tr('gui.help.' + name) != 'gui.help.' + name
    assert window.fields['port'][0].maximumWidth() == 160
    assert window.fields['mic_seg_duration'][0].maximumWidth() == 160
    assert not hasattr(window, 'save_button')
    window.navigate(0)
    assert not window.advanced.isVisible()
    window.navigate(5)
    assert window.advanced.isVisible()
    window.startup_language = 'en'
    widget = window.fields['ui_language'][0]
    widget.setCurrentIndex(widget.findData('zh-CN'))
    settle(qt_app, window)
    assert window.save_status.text() == label('language_restart')
    assert window.field_states['ui_language'].text() == label('restart')


def test_autosave_keeps_latest_runtime_indicator(window, qt_app):
    window.update_runtime({'recording': True, 'connected': True, 'processing_count': 1})
    window.fields['save_audio'][0].setChecked(True)
    settle(qt_app, window)
    assert window.icon_recording and window.latest_state['runtime']['recording']


def test_field_help_hover_click_and_keyboard_do_not_edit_settings(window, qt_app, gui_root, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QToolTip
    from core.settings_gui.help_widgets import HelpButton
    path = gui_root / 'config_client.py'
    original = path.read_bytes()
    window.navigate(0)
    qt_app.processEvents()
    help_button = next(button for button in window.findChildren(HelpButton) if button.field_name == 'ui_language')
    assert help_button.isVisible() and help_button.accessibleDescription()
    point = QPoint(8, 8)
    qt_app.sendEvent(help_button, QHelpEvent(QEvent.Type.ToolTip, point, help_button.mapToGlobal(point)))
    assert QToolTip.isVisible() and QToolTip.text() == help_button.toolTip()
    QToolTip.hideText()
    opened = []
    monkeypatch.setattr(QToolTip, 'showText', lambda *args: opened.append(args))
    help_button.click()
    help_button.setFocus()
    QTest.keyClick(help_button, Qt.Key.Key_F1)
    assert len(opened) == 2 and all(args[1] == help_button.toolTip() for args in opened)
    assert not window.changes() and not window.autosave.isActive()
    assert path.read_bytes() == original


def test_settings_groups_reflow_without_losing_widgets_or_horizontal_overflow(window, qt_app, gui_root):
    from core.settings_gui.help_widgets import SettingsGroups
    from PySide6.QtWidgets import QLabel
    original = (gui_root / 'config_client.py').read_bytes()
    controls = {name: widget for name, (widget, _) in window.fields.items()}
    window.navigate(0)
    scroll = window.pages.widget(0)
    groups = scroll.findChild(SettingsGroups)
    for width, expected_columns in ((1440, 2), (800, 1), (1440, 2)):
        window.resize(width, 860)
        for _ in range(8):
            qt_app.processEvents()
        assert groups.columns == expected_columns
        assert scroll.horizontalScrollBar().maximum() == 0, (
            width, [(frame.minimumSizeHint().width(), frame.width()) for frame in groups.cards],
            window.fields['input_device'][0].parentWidget().minimumSizeHint().width())
        assert {name: widget for name, (widget, _) in window.fields.items()} == controls
        assert all(not widget.accessibleDescription() or not any(
            label.text() == widget.accessibleDescription() for label in groups.findChildren(QLabel))
            for widget in controls.values())
    assert not window.changes() and not window.autosave.isActive()
    assert (gui_root / 'config_client.py').read_bytes() == original


@pytest.mark.parametrize('accept', [False, True])
def test_exit_dialog_has_safe_default_and_explicit_buttons(window, qt_app, accept):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from core.settings_gui.window import label
    inspected = []

    def respond():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert dialog.defaultButton().text() == label('cancel')
        assert dialog.escapeButton() is dialog.defaultButton()
        inspected.append(dialog.text())
        next(button for button in dialog.buttons()
             if button.text() == label('exit_client' if accept else 'cancel')).click()

    window.latest_state['runtime'] = {'recording': True}
    QTimer.singleShot(0, respond)
    assert window.confirm_exit() is accept
    assert label('exit_active') in inspected[0]


def test_cancel_exit_keeps_client_running_and_confirmed_exit_flushes_edits(window, qt_app, gui_root, monkeypatch):
    calls = []
    dispatch = window.backend.dispatch
    def controlled(method, params):
        calls.append(method)
        return None if method == 'desktop_stop' else dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', controlled)
    monkeypatch.setattr(window, 'confirm_exit', lambda: False)
    window.request_exit()
    assert not window.exit_pending and 'desktop_stop' not in calls
    window.fields['save_audio'][0].setChecked(True)
    monkeypatch.setattr(window, 'confirm_exit', lambda: True)
    monkeypatch.setattr(window, 'finish_exit', lambda: None)
    window.request_exit()
    settle(qt_app, window)
    assert calls.index('save') < calls.index('desktop_stop')
    assert Backend(gui_root).config()['save_audio'] and not window.changes()


def test_exit_is_cancelled_when_pending_autosave_fails(window, qt_app, monkeypatch):
    dispatch = window.backend.dispatch
    def controlled(method, params):
        if method == 'save':
            raise OSError('synthetic failure')
        assert method != 'desktop_stop'
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', controlled)
    monkeypatch.setattr(window, 'confirm_exit', lambda: True)
    window.fields['save_audio'][0].setChecked(True)
    window.request_exit()
    settle(qt_app, window)
    assert window.save_error and not window.exiting and not window.exit_pending


def test_ready_microphone_is_waiting_until_recording_flag_changes(window):
    from core.i18n import tr
    runtime = {'connected': True, 'recording': False, 'paused': False,
               'microphone_ready': True, 'processing_count': 0}
    window.home.update_snapshot({'runtime': runtime})
    assert window.home.headline.text() == tr('gui.home_ready')
    assert window.home.detail.text() == tr('gui.home_ready_hint')
    assert window.home.mark.recording is False
    runtime['recording'] = True
    window.home.update_snapshot({'runtime': runtime})
    assert window.home.headline.text() == tr('gui.recording')
    assert window.home.detail.text() == tr('gui.recording_hint')
    assert window.home.mark.recording is True
    runtime.update(recording=False, processing_count=1)
    window.home.update_snapshot({'runtime': runtime})
    assert window.home.headline.text() == tr('gui.home_processing')
    assert window.home.detail.text() == tr('gui.processing_hint')
    assert window.home.mark.recording is False


def history_dates(page, start='2026-09-01', end='2026-09-30'):
    from PySide6.QtCore import QDate
    page.period.setCurrentIndex(page.period.findData('custom'))
    page.date_from.setDate(QDate.fromString(start, 'yyyy-MM-dd'))
    page.date_to.setDate(QDate.fromString(end, 'yyyy-MM-dd'))


def test_history_next_after_selection_completes(window, qt_app, gui_root, monkeypatch):
    directory = gui_root / 'records/transcripts/2026/09'
    directory.mkdir(parents=True)
    (directory / '28.md').write_text(''.join(f'### 12:00:{i:02d}\n\nResult {i}\n\n'
                                            for i in range(45)), encoding='utf-8')
    calls = []
    dispatch = window.backend.dispatch
    def traced(method, params):
        calls.append(method)
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', traced)
    history_dates(window.history)
    window.show_history()
    settle(qt_app, window)
    window.history.results.setCurrentRow(0)
    settle(qt_app, window)
    calls.clear()
    window.history.next.click()
    settle(qt_app, window)
    assert window.history.page == 1, calls
    assert window.history.results.count() == 15
    assert calls == ['history_query']
    window.history.results.setCurrentRow(0)
    settle(qt_app, window)
    window.history.previous.click()
    settle(qt_app, window)
    assert window.history.page == 0 and window.history.results.count() == 30
    window.history.results.setCurrentRow(0)
    settle(qt_app, window)
    window.history.keyword.setText('Result 44')
    window.history.search.click()
    settle(qt_app, window)
    assert window.history.results.count() == 1
    assert not window.history.next.isEnabled()


def test_history_search_detail_copy_and_file_open_are_explicit(window, qt_app, gui_root, monkeypatch):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QApplication
    directory = gui_root / 'records/transcripts/2026/09'
    directory.mkdir(parents=True)
    path = directory / '28.md'
    path.write_text('### 12:00:00\n\nSynthetic recognition <b>literal</b>\n\n'
                    'Original transcription:\n\nSynthetic original\n', encoding='utf-8')
    clipboard = Mock()
    monkeypatch.setattr(QApplication, 'clipboard', lambda: clipboard)
    launch = Mock()
    monkeypatch.setattr('subprocess.Popen', launch)
    window.fields['save_audio'][0].setChecked(True)
    history_dates(window.history)
    window.history.keyword.setText('recognition')
    window.history_button.click()
    settle(qt_app, window)
    page = window.history
    assert window.workspace.currentIndex() == 2
    assert page.results.count() == 1 and page.detail.toPlainText() == ''
    assert not window.changes() and Backend(gui_root).config()['save_audio']
    assert not page.copy.isEnabled()
    clipboard.setText.assert_not_called()
    launch.assert_not_called()
    page.results.setCurrentRow(0)
    settle(qt_app, window)
    assert '<b>literal</b>' in page.detail.toPlainText()
    assert 'Synthetic original' in page.detail.toPlainText()
    clipboard.setText.assert_not_called()
    page.copy.click()
    clipboard.setText.assert_called_once_with(page.detail.toPlainText())
    page.open_file.click()
    settle(qt_app, window)
    launch.assert_called_once_with(['notepad.exe', str(path)])
    window.show_home()
    window.history_button.click()
    assert page.results.count() == 1


def test_history_uses_saved_directory_and_old_records_with_saving_off(window, qt_app, gui_root):
    from core.settings import SettingsService
    directory = gui_root / 'custom-records/2024/02'
    directory.mkdir(parents=True)
    (directory / '29.md').write_text('### 23:59:59\n\nExisting archive\n', encoding='utf-8')
    service = SettingsService.standalone(gui_root / 'config_client.py')
    service.save({'transcript_dir': 'custom-records', 'save_transcripts': False, 'save_llm_records': False},
                 revision=service.read().revision)
    history_dates(window.history, '2024-02-01', '2024-02-29')
    window.show_history()
    settle(qt_app, window)
    assert window.history.results.count() == 1
    from core.i18n import tr
    assert tr('gui.history_saving_off') in window.history.summary.text()
    assert not (gui_root / 'records').exists()


def test_invalid_history_query_shows_inline_feedback(window, qt_app, monkeypatch):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QMessageBox
    modal = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', modal)
    history_dates(window.history, '2026-09-30', '2026-09-01')
    window.show_history()
    settle(qt_app, window)
    from core.i18n import tr
    assert tr('gui.history_invalid_range') in window.history.progress.text()
    assert window.history.results.count() == 0
    assert window.history.isEnabled() and not window.history.copy.isEnabled()
    modal.assert_not_called()
    window.history.reset.click()
    settle(qt_app, window)
    assert tr('gui.history_empty') in window.history.summary.text()
    assert not window.history.date_from.isEnabled() and not window.history.date_to.isEnabled()


def test_history_calendar_keyword_only_and_combined_filters(window, qt_app, gui_root):
    from PySide6.QtCore import QDate, QPoint, Qt
    from PySide6.QtTest import QTest
    from core.settings_gui.history import day_path
    for day in ('2026-08-31', '2026-09-01'):
        path = day_path(gui_root / 'records/transcripts', day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('### 12:00:00\n\nSynthetic keyword', encoding='utf-8')
    page = window.history
    history_dates(page, '2026-09-01', '2026-09-01')
    page.keyword.setText('KEYWORD')
    window.show_history()
    settle(qt_app, window)
    assert page.results.count() == 1
    assert page.date_from.calendarPopup() and page.date_to.calendarPopup()
    assert page.date_from.calendarWidget().selectedDate() == QDate(2026, 9, 1)
    QTest.mouseClick(page.date_from, Qt.MouseButton.LeftButton,
                     pos=QPoint(page.date_from.width() - 10, page.date_from.height() // 2))
    qt_app.processEvents()
    assert page.date_from.calendarWidget().isVisible()
    QTest.keyClick(page.date_from.calendarWidget(), Qt.Key.Key_Escape)
    page.period.setCurrentIndex(page.period.findData('all'))
    settle(qt_app, window)
    assert page.results.count() == 2 and page.query == {'keyword': 'KEYWORD'}
    page.reset.click()
    settle(qt_app, window)
    assert page.keyword.text() == '' and page.results.count() == 2


def test_history_selection_details_costs_and_copy_result(window, qt_app, gui_root, monkeypatch):
    from contextlib import closing
    from datetime import datetime
    import sqlite3
    from unittest.mock import Mock
    from PySide6.QtWidgets import QApplication
    from core.client.diary.diary_writer import DiaryWriter
    from core.i18n import tr
    DiaryWriter(gui_root / 'records/transcripts').write(
        'Final result', datetime(2026, 9, 28, 12).timestamp(), original='Raw recognition',
        action_input='Action input', action_output='Final result', task_id='task', request_id='request',
        preset_id='example', system_prompt='Historical prompt')
    directory = gui_root / 'llm-costs'
    directory.mkdir()
    with closing(sqlite3.connect(directory / '2026-09.sqlite3')) as db, db:
        db.execute('CREATE TABLE requests (id TEXT PRIMARY KEY, started TEXT, record TEXT)')
        db.execute('INSERT INTO requests VALUES (?, ?, ?)', ('request', '2026-09', json.dumps({
            'provider': 'synthetic', 'model': 'test-model', 'status': 'completed', 'elapsed_ms': 1500,
            'accounting': {'amount': '0.000123', 'currency': 'USD', 'cost_source': 'rate_estimate',
                           'usage_source': 'provider', 'usage': {'input_tokens': 123, 'output_tokens': 45}}})))
    clipboard = Mock()
    monkeypatch.setattr(QApplication, 'clipboard', lambda: clipboard)
    history_dates(window.history)
    window.show_history()
    settle(qt_app, window)
    page = window.history
    page.results.setCurrentRow(0)
    settle(qt_app, window)
    assert page.stage_fields['original'].toPlainText() == 'Raw recognition'
    assert page.stage_fields['input'].toPlainText() == 'Action input'
    assert page.stage_fields['output'].toPlainText() == 'Final result'
    assert page.stage_fields['final'].toPlainText() == 'Final result'
    assert page.stage_fields['prompt'].toPlainText() == 'Historical prompt'
    assert 'USD 0.000123' in page.record_meta.text()
    assert tr('gui.history_cost_rate_estimate') in page.record_meta.text()
    assert 'test-model' in page.cost_detail.text() and '123' in page.cost_detail.text()
    assert page.copy_final.isEnabled() and not page.tabs.isHidden()
    clipboard.setText.assert_not_called()
    page.copy_final.click()
    clipboard.setText.assert_called_once_with('Final result')


def test_history_query_waits_for_quiet_poll_without_stalling(window, qt_app, gui_root, monkeypatch):
    import threading
    from core.settings_gui.history import day_path
    path = day_path(gui_root / 'records/transcripts', '2026-09-28')
    path.parent.mkdir(parents=True)
    path.write_text('### 12:00:00\n\nSaved record', encoding='utf-8')
    history_dates(window.history)
    window.show_history()
    settle(qt_app, window)
    window.history.results.setCurrentRow(0)
    settle(qt_app, window)
    release = threading.Event()
    dispatch = window.backend.dispatch
    def delayed(method, params):
        if method == 'read':
            assert release.wait(3)
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', delayed)
    try:
        window.request('read', {}, lambda _: None, quiet=True)
        window.history.search.click()
        assert window.pending_request[0] == 'history_query'
        window.history.fetch(0, {'keyword': 'latest'})
        assert window.history.query == {'keyword': 'latest'}
    finally:
        release.set()
    settle(qt_app, window)
    assert window.history.results.count() == 0
    assert window.history.isEnabled() and window.pending_request is None


def test_history_preset_dates_and_keyword_apply_without_submit(window, qt_app, gui_root):
    from PySide6.QtCore import QDate
    from PySide6.QtTest import QTest
    from core.settings_gui.history import day_path
    today = QDate.currentDate()
    for days, value in ((0, 'Alpha'), (-5, 'Beta'), (-40, 'Older')):
        path = day_path(gui_root / 'records/transcripts', today.addDays(days).toString('yyyy-MM-dd'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('### 12:00:00\n\n' + value, encoding='utf-8')
    page = window.history
    page.period.setCurrentIndex(page.period.findData('all'))
    window.show_history()
    settle(qt_app, window)
    assert page.results.count() == 3
    page.period.setCurrentIndex(page.period.findData('today'))
    settle(qt_app, window)
    assert page.results.count() == 1 and 'Alpha' in page.results.item(0).text()
    page.period.setCurrentIndex(page.period.findData('week'))
    settle(qt_app, window)
    assert page.results.count() == 2
    QTest.keyClicks(page.keyword, 'Beta')
    settle(qt_app, window)
    assert page.results.count() == 1 and 'Beta' in page.results.item(0).text()
    page.date_to.setDate(today.addDays(-6))
    settle(qt_app, window)
    assert page.period.currentData() == 'custom' and page.results.count() == 0


def test_history_jump_enter_and_page_bounds(window, qt_app, gui_root):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from core.settings_gui.history import day_path
    path = day_path(gui_root / 'records/transcripts', '2026-09-28')
    path.parent.mkdir(parents=True)
    path.write_text(''.join(f'### 12:{i // 60:02d}:{i % 60:02d}\n\nResult {i}\n\n'
                            for i in range(95)), encoding='utf-8')
    page = window.history
    history_dates(page)
    window.show_history()
    settle(qt_app, window)
    assert page.page_number.maximum() == 4
    page.page_number.setValue(3)
    page.jump.click()
    settle(qt_app, window)
    assert page.page == 2 and page.results.count() == 30
    page.page_number.setValue(999)
    QTest.keyClick(page.page_number.lineEdit(), Qt.Key.Key_Return)
    settle(qt_app, window)
    assert page.page == 3 and page.results.count() == 5 and not page.next.isEnabled()
    page.keyword.setText('Result 94')
    settle(qt_app, window)
    assert page.page == 0 and page.page_number.maximum() == 1
    assert not page.jump.isEnabled()


def test_history_refresh_keeps_controls_and_content_stable(window, qt_app, gui_root, monkeypatch):
    import threading
    from PySide6.QtCore import QEvent, QObject
    from core.settings_gui.history import day_path
    path = day_path(gui_root / 'records/transcripts', '2026-09-28')
    path.parent.mkdir(parents=True)
    path.write_text('### 12:00:00\n\nSaved content', encoding='utf-8')
    page = window.history
    history_dates(page)
    window.show_history()
    settle(qt_app, window)
    page.results.setCurrentRow(0)
    settle(qt_app, window)
    before = page.detail.toPlainText()
    changes = []
    class Observer(QObject):
        def eventFilter(self, watched, event):
            if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.Hide):
                changes.append((watched, event.type()))
            return False
    observer = Observer(page)
    controls = (page.search, page.reset, page.period, page.copy_final, page.open_file,
                window.advanced, window.exit_button, page.tabs)
    for control in controls:
        control.installEventFilter(observer)
    release = threading.Event()
    dispatch = window.backend.dispatch
    def delayed(method, params):
        if method == 'history_query':
            assert release.wait(3)
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', delayed)
    try:
        page.search.click()
        qt_app.processEvents()
        assert page.results.count() == 1 and page.detail.toPlainText() == before
        assert page.copy_final.isEnabled() and page.period.isEnabled()
        assert not page.tabs.isHidden()
    finally:
        release.set()
    settle(qt_app, window)
    assert page.detail.toPlainText() == before and page.results.currentRow() == 0
    assert changes == []


def test_history_latest_filter_wins_over_slow_error(window, qt_app, gui_root, monkeypatch):
    import threading
    from core.settings_gui.history import day_path
    from core.i18n import Notice
    path = day_path(gui_root / 'records/transcripts', '2026-09-28')
    path.parent.mkdir(parents=True)
    path.write_text('### 12:00:00\n\nFinal choice', encoding='utf-8')
    history_dates(window.history)
    window.show_history()
    settle(qt_app, window)
    release = threading.Event()
    dispatch = window.backend.dispatch
    calls = []
    def delayed(method, params):
        if method == 'history_query':
            calls.append(params['keyword'])
            if params['keyword'] == 'obsolete':
                assert release.wait(3)
                raise ValueError(Notice('gui.history_unavailable'))
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', delayed)
    page = window.history
    try:
        page.fetch(0, {'keyword': 'obsolete'})
        page.fetch(0, {'keyword': 'intermediate'})
        page.fetch(0, {'keyword': 'Final choice'})
    finally:
        release.set()
    settle(qt_app, window)
    assert calls == ['obsolete', 'Final choice']
    assert page.results.count() == 1 and page.progress.text() == ''
    assert page.query == {'keyword': 'Final choice'}


def test_history_empty_hint_is_centered_and_not_a_record_heading(window, qt_app):
    from PySide6.QtCore import Qt
    window.show_history()
    settle(qt_app, window)
    page = window.history
    assert page.record_title.isHidden() and not page.empty_hint.isHidden()
    assert page.empty_hint.alignment() == Qt.AlignmentFlag.AlignCenter
    assert abs(page.empty_hint.geometry().center().y() - page.empty_space.rect().center().y()) <= 2


def test_history_rapid_selection_preserves_display_until_latest_detail_arrives(window, qt_app, gui_root, monkeypatch):
    import threading
    from core.settings_gui.history import day_path
    path = day_path(gui_root / 'records/transcripts', '2026-09-28')
    path.parent.mkdir(parents=True)
    path.write_text(''.join(f'### 12:00:0{i}\n\nResult {i}\n\n' for i in range(3)), encoding='utf-8')
    page = window.history
    history_dates(page)
    window.show_history()
    settle(qt_app, window)
    page.results.setCurrentRow(0)
    settle(qt_app, window)
    release = threading.Event()
    dispatch = window.backend.dispatch
    def delayed(method, params):
        if method == 'history_read':
            assert release.wait(3)
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', delayed)
    shown = []
    show_entry = page.show_entry
    def track(result, entry):
        shown.append(result['stages']['final'])
        show_entry(result, entry)
    monkeypatch.setattr(page, 'show_entry', track)
    try:
        page.results.setCurrentRow(1)
        page.results.setCurrentRow(2)
        qt_app.processEvents()
        assert page.final_text == 'Result 2'
        assert page.copy_final.isEnabled() and not page.tabs.isHidden()
    finally:
        release.set()
    settle(qt_app, window)
    assert shown == ['Result 0'] and page.final_text == 'Result 0'


def test_history_shutdown_drops_deferred_reads(window, qt_app, gui_root, monkeypatch):
    import threading
    release = threading.Event()
    dispatch = window.backend.dispatch
    calls = []
    def delayed(method, params):
        calls.append(method)
        assert release.wait(3)
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', delayed)
    page = window.history
    try:
        window.show_history()
        page.fetch(0, {'keyword': 'pending'})
        page.stop_queries()
    finally:
        release.set()
    settle(qt_app, window)
    assert calls == ['history_query']
    assert page.pending is None and not page.retry.isActive() and not page.auto_search.isActive()


def test_desktop_history_file_launch_belongs_to_gui_and_uses_effective_path(gui_root, monkeypatch):
    from unittest.mock import Mock
    from core.settings_gui.desktop import DesktopBackend
    session = Mock()
    session.call.return_value = str(gui_root / 'effective-records/2026/09/28.md')
    backend = DesktopBackend(gui_root)
    backend.phase, backend.session = 'running', session
    launch = Mock()
    monkeypatch.setattr('core.settings_gui.desktop.subprocess.Popen', launch)
    backend.dispatch('history_open', {'day': '2026-09-28'})
    session.call.assert_called_once_with('history_open_path', {'day': '2026-09-28'})
    launch.assert_called_once_with(['notepad.exe', session.call.return_value])


def test_subtle_buttons_have_visible_hover_and_keyboard_feedback(window, qt_app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    window.exit_button.show()
    for button in (window.exit_button, window.advanced, window.history_button):
        window.navigate(5) if button is window.advanced else window.show_home()
        qt_app.processEvents()
        button.ensurePolished()
        QTest.mouseMove(window, QPoint(2, 2))
        qt_app.processEvents()
        before = button.grab().toImage()
        QTest.mouseMove(button, button.rect().center())
        qt_app.processEvents()
        assert button.grab().toImage() != before
        assert button.cursor().shape() == Qt.CursorShape.PointingHandCursor
        QTest.mouseMove(window, QPoint(2, 2))
        button.setFocus()
        qt_app.processEvents()
        assert button.hasFocus()


def test_runtime_status_does_not_read_settings_or_catalog(gui_root, monkeypatch):
    from concurrent.futures import Future
    from unittest.mock import Mock
    result = Future()
    result.set_result({'recording': True})
    operations = Mock()
    operations.submit.return_value = result
    backend = Backend(gui_root, operations)
    monkeypatch.setattr(backend, 'setting', lambda *_a, **_k: pytest.fail('settings read'))
    monkeypatch.setattr(backend, 'editor', lambda: pytest.fail('catalog read'))
    assert backend.dispatch('status', {}) == {'recording': True}


def test_desktop_status_failure_clears_recording_without_restarting_client(gui_root):
    from unittest.mock import Mock
    from core.settings_gui.desktop import DesktopBackend
    session = Mock()
    backend = DesktopBackend(gui_root, Mock(return_value=session))
    backend.session, backend.phase = session, 'running'
    session.call.return_value = {'recording': True}
    assert backend.dispatch('status', {}) == {'recording': True}
    session.call.assert_called_once_with('status', {})
    session.call.side_effect = EOFError()
    assert backend.dispatch('status', {}) is None
    assert backend.phase == 'failed'
    session.close.assert_called_once_with(graceful=False)
    backend.factory.assert_not_called()


def test_failed_status_reveals_window_once_and_updates_pause_label(window, monkeypatch):
    from unittest.mock import Mock
    from core.i18n import tr
    reveal = Mock()
    monkeypatch.setattr('core.settings_gui.window.show_window', reveal)
    window.desktop_mode = True
    failed = {**window.snapshot, 'desktop': {'phase': 'failed', 'message': 'Synthetic failure'}}
    window.update_state(failed)
    window.update_state(failed)
    reveal.assert_called_once()
    paused = {**window.snapshot, 'runtime': {'connected': True, 'paused': True}}
    window.update_state(paused)
    assert window.home.pause_button.text() == tr('gui.resume')
    window.desktop_mode = False


def test_desktop_without_tray_closes_through_explicit_exit(window, monkeypatch):
    from unittest.mock import Mock
    exit_request = Mock()
    monkeypatch.setattr(window, 'request_exit', exit_request)
    window.desktop_mode = True
    window.close()
    exit_request.assert_called_once()
    assert not window.closed.is_set()
    window.desktop_mode = False


def test_shutdown_failure_reenables_exit_then_success_reaps_window_worker(window, qt_app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from unittest.mock import Mock
    dispatch = window.backend.dispatch
    failing = [True]

    def controlled(method, params):
        if method == 'desktop_stop':
            if failing[0]:
                raise OSError('synthetic shutdown failure')
            return None
        return dispatch(method, params)

    monkeypatch.setattr(window.backend, 'dispatch', controlled)
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    monkeypatch.setattr(window, 'confirm_exit', lambda: True)
    window.desktop_mode = True
    window.request_exit()
    settle(qt_app, window)
    assert not window.exiting and window.exit_button.isEnabled()
    failing[0] = False
    window.request_exit()
    settle(qt_app, window)
    assert window.closed.is_set() and not window.thread.is_alive()


@pytest.mark.parametrize(('minimized', 'has_tray'), [(True, True), (True, False), (False, True)])
def test_desktop_startup_visibility(gui_root, qt_app, monkeypatch, minimized, has_tray):
    from core.settings_gui.main import main
    from PySide6.QtWidgets import QApplication
    from unittest.mock import Mock
    settings = Backend(gui_root)
    before = settings.dispatch('read', {})
    settings.dispatch('save', {'changes': {'start_minimized': minimized}, 'revision': before['revision']})
    presence = Mock()
    presence.acquire.return_value = True
    monkeypatch.setattr('core.settings_gui.shell.DesktopPresence', lambda _: presence)
    monkeypatch.setattr('core.settings_gui.desktop.DesktopBackend', Backend)
    monkeypatch.setattr('core.settings_gui.shell.install_tray', lambda *_: Mock() if has_tray else None)
    observed = []

    def event_loop(_):
        window = presence.bind.call_args.args[0]
        settle(qt_app, window)
        observed.append(window.isVisible())
        window.closed.set()
        window.close()
        return 0

    # The backend fixture owns no client process or hardware.
    monkeypatch.setattr(Backend, 'session', None, raising=False)
    monkeypatch.setattr(QApplication, 'exec', event_loop)
    assert main(gui_root, desktop=True) == 0
    assert observed == [not (minimized and has_tray)]
    presence.close.assert_called_once()


@pytest.mark.skipif(os.name != 'nt', reason='Windowed Python is a Windows entry point')
def test_windowed_desktop_connects_to_synthetic_client_and_exits_cleanly(gui_root, monkeypatch):
    import subprocess
    import sys
    write_synthetic_client(gui_root, monkeypatch)
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    script = '''
import ctypes, json, time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from core.settings_gui.main import main
from core.settings_gui.window import SettingsWindow
kernel = ctypes.WinDLL('kernel32')
kernel.SetErrorMode(kernel.GetErrorMode() | 3)
app = QApplication([])
started = time.monotonic()
observed = []
def check():
    for window in app.topLevelWidgets():
        if not isinstance(window, SettingsWindow):
            continue
        if window.backend.phase == 'running' and not observed:
            observed.append(window.backend.session.process)
            window.confirm_exit = lambda: True
            window.request_exit()
        elif time.monotonic() - started > 12:
            app.exit(2)
timer = QTimer()
timer.timeout.connect(check)
timer.start(50)
code = main(Path.cwd(), desktop=True)
assert code == 0 and observed
assert observed[0].returncode == 0
Path('desktop-result.json').write_text(json.dumps({'desktop': code, 'client': observed[0].returncode}))
'''
    script_path = gui_root / 'desktop_probe.py'
    script_path.write_text(script, encoding='utf-8')
    process = subprocess.run([str(Path(sys.executable).with_name('pythonw.exe')), str(script_path)],
                             cwd=gui_root, capture_output=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert json.loads((gui_root / 'desktop-result.json').read_text()) == {'desktop': 0, 'client': 0}



def test_cleanup_provider_save_retains_custom_prompts_context_and_translation(window, qt_app, gui_root):
    editor = CatalogEditor(gui_root / 'LLM')
    before = editor.read()
    editor.save('presets', 'correct_asr', {'prompt_mode': 'custom', 'system_prompt': 'Keep my prompt',
                'use_caret_context': False}, revision=before['revision'])
    window.reload()
    settle(qt_app, window)
    assert not window.cleanup_notice.isHidden()
    window.fields['llm_enabled'][0].setChecked(True)
    provider = window.editors['presets'][2]['provider'][0]
    provider.setCurrentIndex(provider.findData('local'))
    settle(qt_app, window)
    provider_choice = window.editors['presets'][2]['provider'][0]
    provider_choice.activated.emit(provider_choice.currentIndex())
    settle(qt_app, window)
    after = editor.read()
    assert after['presets']['correct_asr']['system_prompt'] == 'Keep my prompt'
    assert after['presets']['correct_asr']['use_caret_context'] is False
    assert after['presets']['translate'] == before['presets']['translate']


def test_configured_provider_selection_never_edits_connection_or_other_presets(window, qt_app, gui_root):
    editor = CatalogEditor(gui_root / 'LLM')
    editor.save('providers', 'synthetic', {'kind': 'openai', 'base_url': 'https://example.invalid/v1',
                'api_key': 'synthetic-key', 'model': 'synthetic-model'}, revision=editor.read()['revision'])
    window.reload()
    settle(qt_app, window)
    before = (gui_root / 'LLM/providers.toml').read_bytes()
    window.fields['llm_enabled'][0].setChecked(True)
    fields = window.editors['presets'][2]
    fields['provider'][0].setCurrentIndex(fields['provider'][0].findData('synthetic'))
    settle(qt_app, window)
    provider_choice = window.editors['presets'][2]['provider'][0]
    provider_choice.activated.emit(provider_choice.currentIndex())
    settle(qt_app, window)
    assert window.catalog['presets']['correct_asr']['provider'] == 'synthetic'
    assert window.catalog['presets']['translate']['provider'] == 'gemini'
    assert (gui_root / 'LLM/providers.toml').read_bytes() == before
    assert 'synthetic-key' not in repr(window.catalog)
    assert 'providers' not in window.editors


def test_fixed_cleanup_editor_can_restore_absent_builtin_without_altering_advanced_default(window, qt_app, gui_root):
    from core.settings import SettingsService
    service = SettingsService.standalone(gui_root / 'config_client.py')
    service.save({'llm_default_preset': None}, revision=service.read().revision)
    editor = CatalogEditor(gui_root / 'LLM')
    editor.save('presets', 'correct_asr', {}, revision=editor.read()['revision'], delete=True)
    window.reload()
    settle(qt_app, window)
    assert window.editor_ids['presets'] == 'correct_asr'
    window.fields['llm_enabled'][0].setChecked(True)
    provider = window.editors['presets'][2]['provider'][0]
    provider.setCurrentIndex(provider.findData('local'))
    settle(qt_app, window)
    provider_choice = window.editors['presets'][2]['provider'][0]
    provider_choice.activated.emit(provider_choice.currentIndex())
    settle(qt_app, window)
    assert window.catalog['presets']['correct_asr']['prompt_mode'] == 'correction'
    assert service.read().saved['ClientConfig']['llm_default_preset'] is None


def test_text_page_keeps_advanced_preset_controls_out_of_normal_ui(window, qt_app):
    from PySide6.QtWidgets import QFrame
    window.navigate(1)
    qt_app.processEvents()
    assert set(window.editors['presets'][2]) == {'provider'}
    assert not {'llm_default_preset', 'llm_correction_enabled', 'llm_translation_enabled'} & window.fields.keys()
    assert not hasattr(window, 'provider_panel')
    assert not hasattr(window, 'provider_toggle')
    assert 'providers' not in window.editors
    assert window.pages.currentWidget().findChildren(QFrame, 'settingDivider')
    assert window.editors['presets'][0] is None and window.editors['presets'][1] is None


def test_preset_file_open_uses_effective_worker_path_and_parent_launch(gui_root, monkeypatch):
    from core.settings_gui.desktop import DesktopBackend
    from unittest.mock import Mock
    path = str(gui_root / 'effective-llm/presets.toml')
    session = Mock()
    session.call.return_value = path
    backend = DesktopBackend(gui_root)
    backend.phase = 'running'
    backend.session = session
    launch = Mock()
    monkeypatch.setattr('subprocess.Popen', launch)
    backend.dispatch('advanced', {'file': 'presets'})
    session.call.assert_called_once_with('advanced_path', {'file': 'presets'})
    launch.assert_called_once_with(['notepad.exe', path])
    assert Backend(gui_root).dispatch('advanced_path', {'file': 'presets'}) == str(gui_root / 'LLM/presets.toml')


@pytest.mark.parametrize('environment_key, expected', [('', False), ('  ', False), ('synthetic-env-key', True)])
def test_provider_readiness_uses_transport_key_precedence_without_exposing_keys(gui_root, monkeypatch,
                                                                              environment_key, expected):
    monkeypatch.setenv('CAPSWRITER_TEST_PROVIDER_KEY', environment_key)
    editor = CatalogEditor(gui_root / 'LLM')
    result = editor.save('providers', 'gemini', {'api_key': 'synthetic-file-key',
                         'api_key_env': 'CAPSWRITER_TEST_PROVIDER_KEY'}, revision=editor.read()['revision'])
    assert result['providers']['gemini']['credentials_ready'] is expected
    assert result['providers']['local']['credentials_ready']
    assert 'synthetic-file-key' not in repr(result) and 'synthetic-env-key' not in repr(result)
    monkeypatch.delenv('CAPSWRITER_TEST_PROVIDER_KEY')
    assert not editor.read()['providers']['gemini']['credentials_ready']


def test_provider_choices_keep_saved_unavailable_entry_without_auto_replacement(window, qt_app, gui_root):
    fields = window.editors['presets'][2]
    provider = fields['provider'][0]
    assert provider.currentData() == 'gemini'
    assert not provider.model().item(provider.findData('gemini')).isEnabled()
    assert provider.model().item(provider.findData('local')).isEnabled()
    assert not hasattr(window, 'provider_save')
    assert not window.provider_notice.isHidden()
    original = (gui_root / 'LLM/presets.toml').read_bytes()
    window.fields['llm_enabled'][0].setChecked(True)
    settle(qt_app, window)
    assert provider.isEnabled()
    provider.setCurrentIndex(provider.findData('local'))
    assert provider.isEnabled() and window.provider_notice.isHidden()
    assert (gui_root / 'LLM/presets.toml').read_bytes() == original
    window.fields['llm_enabled'][0].setChecked(False)
    settle(qt_app, window)
    assert not provider.isEnabled() and window.entry_dirty('presets')
    window.fields['llm_enabled'][0].setChecked(True)
    settle(qt_app, window)
    assert provider.currentData() == 'local' and provider.isEnabled()


def test_no_providers_shows_guidance_without_creating_configuration(window, qt_app, gui_root):
    (gui_root / 'LLM/providers.template.toml').write_text('[providers]\n', encoding='utf-8')
    (gui_root / 'LLM/presets.toml').write_text('[presets]\n', encoding='utf-8')
    window.reload()
    settle(qt_app, window)
    window.fields['llm_enabled'][0].setChecked(True)
    settle(qt_app, window)
    assert not window.provider_notice.isHidden()
    assert not hasattr(window, 'provider_save')
    assert not (gui_root / 'LLM/providers.toml').exists()
    assert not window.entry_dirty('presets')


def test_llm_master_gates_related_controls_and_retains_values_through_refresh(window, qt_app, gui_root):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from core.settings_gui.window import get_value
    window.navigate(1)
    master = window.fields['llm_enabled'][0]
    names = ['llm_correction_level', 'llm_correction_numbers', 'llm_correction_punctuation',
             'llm_correction_fillers', 'llm_correction_english', 'llm_correction_homophones',
             'caret_context_enabled', 'caret_context_before_chars', 'caret_context_after_chars',
             'save_llm_records', 'save_llm_context', 'llm_cost_tracking', 'diagnostic_include_context']
    original = {name: get_value(*window.fields[name]) for name in names}
    assert master.isEnabled() and not master.isChecked()
    assert all(not window.fields[name][0].isEnabled() for name in names)
    toggle = window.fields['llm_correction_numbers'][0]
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert get_value(*window.fields['llm_correction_numbers']) == original['llm_correction_numbers']
    QTest.mouseClick(master, Qt.MouseButton.LeftButton)
    settle(qt_app, window)
    assert master.isChecked() and all(window.fields[name][0].isEnabled() for name in names)
    QTest.mouseClick(master, Qt.MouseButton.LeftButton)
    settle(qt_app, window)
    window.reload()
    settle(qt_app, window)
    window.enable_editor(False)
    window.enable_editor(True)
    assert master.isEnabled() and not master.isChecked()
    assert all(not window.fields[name][0].isEnabled() for name in names)
    assert not window.editors['presets'][2]['provider'][0].isEnabled()
    assert not window.provider_info['model'].isEnabled()
    assert not hasattr(window, 'provider_save')
    assert all(window.fields[name][0].isEnabled() for name in ('save_audio', 'save_transcripts', 'language'))
    config = Backend(gui_root).config()
    assert not config['llm_enabled']
    assert {name: get_value(*window.fields[name]) for name in names} == original
    assert all(config[name] == value for name, value in original.items())


@pytest.fixture
def provider_rates(gui_root):
    import tomlkit
    editor = CatalogEditor(gui_root / 'LLM')
    provider = editor.read()['providers']['local']
    rate = {'endpoint': provider['base_url'], 'model': provider['model'], 'currency': 'USD',
            'input': '0', 'output': '2.50', 'cached_input': '0.03',
            'source': 'https://example.invalid/pricing', 'assumption': 'Synthetic rates',
            'updated': '2026-01-01', 'valid_until': '2099-01-01'}
    def write(**changes):
        (gui_root / 'LLM/costs.toml').write_text(tomlkit.dumps({'rates': [{**rate, **changes}]}), encoding='utf-8')
    write()
    return write


@pytest.mark.parametrize('change, status', [({}, 'configured'), ({'model': 'different'}, 'missing'),
                         ({'endpoint': 'https://different.invalid/v1'}, 'missing'),
                         ({'valid_until': '2026-01-02'}, 'expired')])
def test_provider_pricing_matches_configured_model_and_endpoint(gui_root, provider_rates, change, status):
    provider_rates(**change)
    provider = CatalogEditor(gui_root / 'LLM').read()['providers']['local']
    assert provider['pricing_status'] == status
    if status == 'missing':
        assert provider['pricing'] is None
    else:
        assert provider['pricing']['input'] == '0'
        assert provider['pricing']['output'] == '2.50'
        assert 'endpoint' not in provider['pricing']


def test_provider_pricing_failure_does_not_block_selection(gui_root):
    (gui_root / 'LLM/costs.toml').write_text('invalid [', encoding='utf-8')
    editor = CatalogEditor(gui_root / 'LLM')
    result = editor.save('presets', 'correct_asr', {'provider': 'local'}, revision=editor.read()['revision'])
    assert result['providers']['local']['pricing_status'] == 'invalid'
    assert result['providers']['local']['credentials_ready']
    assert result['presets']['correct_asr']['provider'] == 'local'


def test_provider_details_refresh_without_discarding_selection_draft(window, qt_app, provider_rates):
    from core.settings_gui.window import label
    window.fields['llm_enabled'][0].setChecked(True)
    fields = window.editors['presets'][2]
    fields['provider'][0].setCurrentIndex(fields['provider'][0].findData('local'))
    settle(qt_app, window)
    window.catalog_polled(window.backend.dispatch('catalog', {}))
    assert window.entry_dirty('presets')
    assert set(fields) == {'provider'}
    assert window.provider_info['model'].text() == window.catalog['providers']['local']['model']
    assert '0 USD' in window.provider_info['price_input'].text()
    assert '2.50 USD' in window.provider_info['price_output'].text()
    provider_rates(output='8', currency='CNY')
    window.catalog_polled(window.backend.dispatch('catalog', {}))
    assert window.entry_dirty('presets') and fields['provider'][0].currentData() == 'local'
    assert '8 CNY' in window.provider_info['price_output'].text()
    fields['provider'][0].setCurrentIndex(fields['provider'][0].findData('gemini'))
    assert window.provider_info['model'].text() == window.catalog['providers']['gemini']['model']
    assert window.provider_info['price_input'].text() == label('price_unknown')
    assert window.provider_info['price_cached'].isHidden()



def test_provider_keyboard_selection_autosaves_and_refresh_does_not_write(window, qt_app, gui_root, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window.fields['llm_enabled'][0].setChecked(True)
    settle(qt_app, window)
    editor = CatalogEditor(gui_root / 'LLM')
    original = editor.read()
    writes = []
    dispatch = window.backend.dispatch
    def capture(method, params):
        if method == 'catalog_save':
            writes.append(params)
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', capture)
    window.navigate(1)
    provider = window.editors['presets'][2]['provider'][0]
    provider.setFocus()
    QTest.keyClick(provider, Qt.Key.Key_End)
    settle(qt_app, window)
    assert provider.currentData() == 'local'
    assert editor.read()['presets']['correct_asr']['provider'] == 'local'
    assert editor.read()['presets']['translate'] == original['presets']['translate']
    assert not (gui_root / 'LLM/providers.toml').exists()
    assert not window.entry_dirty('presets') and window.provider_status.isHidden()
    assert len(writes) == 1 and writes[0]['changes'] == {'provider': 'local'}
    window.reload()
    settle(qt_app, window)
    window.catalog_polled(editor.read())
    provider.activated.emit(provider.currentIndex())
    settle(qt_app, window)
    assert len(writes) == 1


def test_provider_autosave_failure_is_inline_and_same_choice_can_retry(window, qt_app, gui_root, monkeypatch):
    from unittest.mock import Mock
    from core.settings_gui.window import label
    from PySide6.QtWidgets import QMessageBox
    window.fields['llm_enabled'][0].setChecked(True)
    settle(qt_app, window)
    original = (gui_root / 'LLM/presets.toml').read_bytes()
    dispatch = window.backend.dispatch
    failing = True
    def fail_once(method, params):
        nonlocal failing
        if method == 'catalog_save' and failing:
            failing = False
            raise OSError('Synthetic failure')
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', fail_once)
    warning = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    provider = window.editors['presets'][2]['provider'][0]
    provider.setCurrentIndex(provider.findData('local'))
    provider.activated.emit(provider.currentIndex())
    settle(qt_app, window)
    assert (gui_root / 'LLM/presets.toml').read_bytes() == original
    assert window.entry_dirty('presets') and provider.isEnabled()
    assert not window.provider_status.isHidden()
    assert window.provider_status.text().startswith(label('provider_save_failed'))
    warning.assert_not_called()
    window.catalog_polled(CatalogEditor(gui_root / 'LLM').read())
    assert not window.provider_status.isHidden()
    provider.activated.emit(provider.currentIndex())
    settle(qt_app, window)
    assert CatalogEditor(gui_root / 'LLM').read()['presets']['correct_asr']['provider'] == 'local'
    assert not window.entry_dirty('presets') and window.provider_status.isHidden()


@pytest.mark.parametrize('external_change', [False, True])
def test_provider_autosave_waits_for_poll_and_protects_external_edits(window, qt_app, gui_root, monkeypatch,
                                                                  external_change):
    import threading
    window.fields['llm_enabled'][0].setChecked(True)
    settle(qt_app, window)
    entered, release = threading.Event(), threading.Event()
    dispatch = window.backend.dispatch
    def delayed_read(method, params):
        if method == 'read':
            result = dispatch(method, params)
            entered.set()
            assert release.wait(3)
            return result
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', delayed_read)
    assert window.request('read', {}, window.polled, quiet=True)
    assert entered.wait(2)
    provider = window.editors['presets'][2]['provider'][0]
    provider.setCurrentIndex(provider.findData('local'))
    provider.activated.emit(provider.currentIndex())
    assert window.pending_request[0] == 'catalog_save'
    assert not provider.isEnabled()
    editor = CatalogEditor(gui_root / 'LLM')
    if external_change:
        editor.save('presets', 'translate', {'temperature': 0.4}, revision=editor.read()['revision'])
    release.set()
    settle(qt_app, window)
    saved = editor.read()
    assert provider.isEnabled()
    if external_change:
        assert saved['presets']['correct_asr']['provider'] == 'gemini'
        assert saved['presets']['translate']['temperature'] == 0.4
        assert not window.provider_status.isHidden() and window.entry_dirty('presets')
        window.catalog_polled(saved)
        assert window.catalog_conflict
    else:
        assert saved['presets']['correct_asr']['provider'] == 'local'
        assert not window.entry_dirty('presets')



@pytest.mark.parametrize('exiting, fail', [(False, False), (True, False), (True, True)])
def test_provider_autosave_finishes_before_close_or_exit(window, qt_app, gui_root, monkeypatch, exiting, fail):
    import threading
    from unittest.mock import Mock
    window.fields['llm_enabled'][0].setChecked(True)
    settle(qt_app, window)
    entered, release = threading.Event(), threading.Event()
    dispatch = window.backend.dispatch
    events = []
    def delayed_read(method, params):
        if method == 'read':
            result = dispatch(method, params)
            entered.set()
            assert release.wait(3)
            return result
        if method == 'catalog_save':
            events.append('save')
            if fail:
                raise OSError('Synthetic failure')
        return dispatch(method, params)
    monkeypatch.setattr(window.backend, 'dispatch', delayed_read)
    window.confirm_discard = Mock(return_value=False)
    window.confirm_exit = lambda: True
    monkeypatch.setattr(window, 'begin_exit', lambda: (events.append('exit'), setattr(window, 'exit_pending', False)))
    window.request('read', {}, lambda _: None, quiet=True)
    assert entered.wait(2)
    provider = window.editors['presets'][2]['provider'][0]
    provider.setCurrentIndex(provider.findData('local'))
    provider.activated.emit(provider.currentIndex())
    if exiting:
        window.request_exit()
    else:
        window.close()
    release.set()
    settle(qt_app, window)
    window.confirm_discard.assert_not_called()
    saved = CatalogEditor(gui_root / 'LLM').read()['presets']['correct_asr']['provider']
    if fail:
        assert saved == 'gemini' and events == ['save']
        assert not window.exit_pending and not window.provider_status.isHidden()
    else:
        assert saved == 'local'
        assert events == (['save', 'exit'] if exiting else ['save'])
        if not exiting:
            assert window.closed.is_set()
