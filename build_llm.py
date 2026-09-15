"""发行包仅复制公开 LLM 配置，禁止链接包含本机凭据的工作目录。"""

from pathlib import Path
import shutil
import stat
import tomllib


def copy_llm_configuration(source_root, destination_root):
    source = Path(source_root) / "LLM"
    destination = Path(destination_root) / "LLM"
    # 旧 spec 曾创建 LLM junction。遇到遗留链接时停止，不穿过链接覆写本机 Key。
    for path in (
        destination,
        destination / "providers.toml",
        destination / "providers.template.toml",
        destination / "presets.toml",
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
