"""Package public LLM configuration without linking local credentials."""

from pathlib import Path
import shutil
import stat
import tomllib


def copy_llm_configuration(source_root, destination_root):
    source = Path(source_root) / "LLM"
    destination = Path(destination_root) / "LLM"
    # Older specs created an LLM junction. Stop at legacy links to protect local keys.
    for path in (
        destination,
        destination / "providers.toml",
        destination / "providers.template.toml",
        destination / "presets.toml",
        destination / "costs.template.toml",
    ):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if (
            path.is_symlink()
            or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("LLM package destination must not contain links or junctions")
    template = source / "providers.template.toml"
    with template.open("rb") as stream:
        providers = tomllib.load(stream).get("providers", {})
    if any(data.get("api_key") for data in providers.values()):
        raise ValueError("Provider template must not contain API keys")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, destination / "providers.template.toml")
    shutil.copyfile(template, destination / "providers.toml")
    shutil.copyfile(source / "presets.toml", destination / "presets.toml")
    costs = source / "costs.template.toml"
    if costs.exists():
        shutil.copyfile(costs, destination / costs.name)
