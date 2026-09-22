"""Worker bootstrap must survive edited configuration during Windows spawn."""

import pickle
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from core.worker_bootstrap import configuration_snapshot, install_configuration


def test_replacement_aligner_receives_startup_values(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from config_server import ServerConfig
    from core.server.worker.process_manager import ProcessManager
    from core.worker_bootstrap import start_configured_worker

    app = SimpleNamespace(state=SimpleNamespace(align_queue_in=object(), align_queue_out=object()))
    original = ServerConfig.aligner_idle_timeout
    manager = ProcessManager(app)
    monkeypatch.setattr(ServerConfig, "aligner_idle_timeout", original + 100)
    process = SimpleNamespace(start=Mock(), pid=1)
    constructor = Mock(return_value=process)
    monkeypatch.setattr("core.server.worker.process_manager.Process", constructor)
    manager.is_alive = True
    manager._start_aligner_process()
    kwargs = constructor.call_args.kwargs
    assert kwargs["target"] is start_configured_worker
    snapshot, kind, *_ = kwargs["args"]
    assert kind == "aligner"
    assert snapshot["config_server"][1]["ServerConfig"]["aligner_idle_timeout"] == original


def test_snapshot_is_detached_and_preserves_paths_and_sequences(monkeypatch):
    module = ModuleType("synthetic_worker_config")
    module.__version__ = "2.6"
    module.Options = type(
        "Options",
        (),
        {
            "__module__": module.__name__,
            "path": Path("models") / "synthetic",
            "items": [("test", 1)],
        },
    )
    snapshot = configuration_snapshot(module)
    module.Options.items.append(("later", 2))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    install_configuration(pickle.loads(pickle.dumps(snapshot)))
    restored = sys.modules[module.__name__]
    assert restored.Options.path == Path("models") / "synthetic"
    assert restored.Options.items == [("test", 1)]
    assert restored.__version__ == "2.6"


def test_spawn_imports_retained_config_when_disk_file_is_invalid(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "config_server.py").write_text("this is not valid Python!", encoding="utf-8")
    script = tmp_path / "spawn_check.py"
    script.write_text(
        f"""
import sys
import runpy
from multiprocessing import get_context
sys.path.insert(0, {str(root)!r})
from core.worker_bootstrap import install_configuration

def child(snapshot, output):
    # Exercise the actual source entry point's spawn import, without starting it.
    runpy.run_path({str(root / "start_server.py")!r}, run_name='__mp_main__')
    install_configuration(snapshot)
    import config_server
    output.put(config_server.ServerConfig.model_type)

if __name__ == '__main__':
    context = get_context('spawn')
    output = context.Queue()
    snapshot = {{'config_server': ({{'__version__': '2.6'}}, {{'ServerConfig': {{'model_type': 'retained'}}}})}}
    process = context.Process(target=child, args=(snapshot, output))
    process.start()
    try:
        assert output.get(timeout=10) == 'retained'
        process.join(10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
""",
        encoding="utf-8",
    )
    # This isolated bootstrap has no measured business modules. Do not let the
    # parent's pytest-cov auto-start use a different config in its temporary cwd.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE_") and key != "COVERAGE_PROCESS_START"
    }
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
