"""Seed spawned workers with parent-owned configuration before server imports.

Configuration snapshots contain detached Python values. A separate shared locale
value controls display only. No configuration file is written and no edited
source is imported when an idle aligner is replaced.
"""

from core.i18n import Notice

import copy
import sys
from types import ModuleType


def configuration_snapshot(*modules):
    snapshot = {}
    for module in modules:
        classes = {
            name: {
                key: copy.deepcopy(value)
                for key, value in vars(cls).items()
                if not key.startswith("_")
            }
            for name, cls in vars(module).items()
            if isinstance(cls, type) and cls.__module__ == module.__name__
        }
        snapshot[module.__name__] = (
            {
                key: copy.deepcopy(getattr(module, key))
                for key in ("BASE_DIR", "__version__", "__file__")
                if hasattr(module, key)
            },
            classes,
        )
    return snapshot


def install_configuration(snapshot):
    for name, (attributes, classes) in snapshot.items():
        module = ModuleType(name)
        vars(module).update(copy.deepcopy(attributes))
        for class_name, values in classes.items():
            setattr(
                module,
                class_name,
                type(class_name, (), {"__module__": name, **copy.deepcopy(values)}),
            )
        sys.modules[name] = module


def start_configured_worker(snapshot, kind, *args, ui_language=None):
    install_configuration(snapshot)
    from core.i18n import bind_shared_language
    bind_shared_language(ui_language)
    if kind == "asr":
        from core.server.worker import start_worker

        start_worker(*args)
    elif kind == "aligner":
        from core.server.worker.aligner_worker import start_aligner_worker

        start_aligner_worker(*args)
    else:
        raise ValueError(Notice('validation.worker_bootstrap.unknown_worker_kind'))
