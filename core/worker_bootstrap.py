"""Seed spawned workers with parent-owned configuration before server imports.

Only detached Python values cross IPC. No configuration file is written and no
edited source is imported when an idle aligner is replaced.
"""

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


def start_configured_worker(snapshot, kind, *args):
    install_configuration(snapshot)
    if kind == "asr":
        from core.server.worker import start_worker

        start_worker(*args)
    elif kind == "aligner":
        from core.server.worker.aligner_worker import start_aligner_worker

        start_aligner_worker(*args)
    else:
        raise ValueError("Unknown worker kind")
