# Build and inspect a Windows package

The repository contains combined server/client and client-only PyInstaller specs. Builds are development artifacts until their contents and behavior pass release validation. Neither EXE existence nor successful source tests proves a clean-machine package works.

## Prepare a build

Use the environment and source checks in [AGENTS.md](../../AGENTS.md). Confirm the intended package, dependencies, backend binaries, and model policy before starting a resource-intensive build. Current CI uses Python 3.11; historical Win7 and Python 3.8 claims are not compatibility guarantees.

From the repository root, run the selected spec with the project interpreter:

```powershell
python -m PyInstaller build.spec
python -m PyInstaller build-client.spec
```

Run only the variant you need. The combined output is `dist/CapsWriter-Offline`; the client-only output is `dist/CapsWriter-Offline-Client`.

## Inspect package inputs

| Input | Current treatment |
| --- | --- |
| Third-party modules and native dependencies | Collected by PyInstaller under `internal/` |
| Runtime configuration | Copied from `config_templates/` to root `config_client.py` and `config_server.py` as applicable |
| LLM configuration | `build_llm.py` copies public provider defaults and presets; local keys are excluded |
| Project resources and source directories | Specs can create Windows junctions into the checkout |
| Models | Development builds can link the model directory; archive filtering is depth-based |
| Root README and license | Copied when present |

Inspect the actual [combined spec](../../build.spec), [client spec](../../build-client.spec), and [LLM copier](../../build_llm.py) before release. Some legacy optional filenames remain in spec copy lists and are skipped when absent; this guide does not claim a complete packaging cleanup.

`INCLUDE_CUDA_PROVIDER` controls collection of the ONNX CUDA provider. It does not configure every GPU backend. The shared llama binding uses the binary location under `core/server/engines/llama/bin/`; the download pointer pins llama.cpp b7798. Do not replace native binaries with an arbitrary newer ABI.

## Handle junctions safely

A build directory can refer back into the working tree. Before deleting, moving, cleaning, or archiving it, inspect junction targets and resolve the intended paths. Do not treat linked resources or models as disposable copies. The current specs and archiver are not a substitute for an isolated, audited release staging process.

An old LLM junction is rejected by the public-configuration copier to prevent overwriting local credentials. Fix the staging layout deliberately; never replace the real local Provider file with a release template.

## Create an archive

After inspecting the build and ensuring 7-Zip is available:

```powershell
python zip_release.py
```

The script archives available package variants into `release/`. It excludes ZIP downloads and nested model payloads by directory depth; client-only archives also exclude DLLs below `core`. Review actual contents, including top-level model files, before publishing. Depth filtering is not a general private-data scanner.

## Validate before release

The [release smoke workflow](../../.github/workflows/release-smoke.yml) runs quality, builds the combined variant, and checks both EXEs exist. It does not launch the package, test missing-model behavior, verify both variants on clean machines, or establish license/backend compatibility.

Record clean-machine startup, configuration upgrade, models, audio/input, UI/DPI, and archive-content checks before release. Reproducible staging, licensing inventory, and complete artifact validation remain in [TODO](../../TODO.md). Do not claim these are complete based on this documentation change.
