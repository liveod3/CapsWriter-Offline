# Integration tests

Place tests spanning protocol, connections, queues, or multiple components here. `tests/conftest.py` adds the `integration` marker by directory; the default suite excludes it.

Run only applicable non-desktop, non-manual cases with:

```powershell
python -m pytest -m "integration and not windows and not manual"
```

This directory currently contains guidance only. Tests must not depend on real API keys, private audio, or installed large models. Use explicit markers for additional requirements.
