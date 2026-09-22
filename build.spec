# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller configuration.
Targets PyInstaller 6.0 and later.
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules
# from PyInstaller.building.build_main import Analysis, COLLECT
from os.path import join, basename, dirname, exists
from os import walk, makedirs
from shutil import copyfile, rmtree

# Packaging options.

# Include the CUDA provider.
# True: bundle onnxruntime_providers_cuda.dll; requires CUDA and cuDNN at runtime.
# False: omit the CUDA provider for a smaller CPU-only package.
INCLUDE_CUDA_PROVIDER = False

# ====================================================


# Initialize collection lists.
binaries = []
hiddenimports = []
datas = []

# Collect sherpa_onnx files.
try:
    sherpa_datas = collect_data_files('sherpa_onnx', include_py_files=False)

    # Apply INCLUDE_CUDA_PROVIDER to collected files.
    if not INCLUDE_CUDA_PROVIDER:
        # Filter out CUDA provider files.
        filtered_datas = []
        for src, dest in sherpa_datas:
            # Identify CUDA provider files.
            if 'providers_cuda' not in basename(src).lower():
                filtered_datas.append((src, dest))
            else:
                print(f"[INFO] Exclude CUDA provider: {basename(src)}")
        sherpa_datas = filtered_datas

    datas += sherpa_datas
except:
    pass

# Collect Pillow files for tray icons.
try:
    pillow_datas = collect_data_files('PIL', include_py_files=False)
    datas += pillow_datas
    pillow_binaries = collect_all('PIL')
    binaries += pillow_binaries[1]
except:
    pass

# Include modules that static import analysis cannot discover.
hiddenimports += [
    'websockets',
    'websockets.client',
    'websockets.server',
    'rich',
    'rich.console',
    'rich.markdown',
    'rich._unicode_data.unicode17-0-0',
    'keyboard',
    'pyclip', 
    'numpy',
    'sounddevice',
    'typer',
    'srt',
    'sherpa_onnx',
    'PIL',           # Pillow supplies tray images.
    'PIL.Image',
    'comtypes.client',
    'comtypes.tools.codegenerator',
    'pystray._win32',
    'pystray',       # Tray icon library.
]

# # Use .py sources for all modules by patching _get_module_collection_mode.
# import PyInstaller.building.build_main as _bm
# _bm._get_module_collection_mode = lambda md, n, na=False: _bm._ModuleCollectionMode.PY

a_1 = Analysis(
    ['start_server.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['build_hook.py'],
    excludes=['IPython',
              'PySide6', 'PySide2', 'PyQt5',
              'matplotlib', 'wx',
              'funasr', 'pydantic', 'torch',
              ],
    noarchive=True,
)

# Filter DLLs collected by binary dependency analysis.
# PyInstaller discovers these through DLL dependencies.
# Load system CUDA DLLs at runtime instead of bundling them.
filtered_binaries = []
for name, src, type in a_1.binaries:
    src_lower = src.lower() if isinstance(src, str) else ''
    is_system_cuda_dll = (
        '\\nvidia gpu computing toolkit\\cuda\\' in src_lower or
        '\\nvidia\\cudnn\\' in src_lower or
        ('\\cuda\\v' in src_lower and '\\bin\\' in src_lower)
    )
    is_unwanted_onnx_dll = (
        'onnxruntime_providers_cuda.dll' in name.lower() 
    )

    if not is_system_cuda_dll and not is_unwanted_onnx_dll:
        filtered_binaries.append((name, src, type))
    else:
        reason = "system CUDA DLL" if is_system_cuda_dll else "redundant ONNX DLL"
        print(f"[INFO] Exclude {reason}: {name} (collected from {src})")
a_1.binaries = filtered_binaries

a_2 = Analysis(
    ['start_client.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['build_hook.py'],
    excludes=['IPython',
              'PySide6', 'PySide2', 'PyQt5',
              'matplotlib', 'wx',
              ],
    noarchive=True,
)

# Apply the same system CUDA exclusions to the client.
filtered_binaries = []
for name, src, type in a_2.binaries:
    src_lower = src.lower() if isinstance(src, str) else ''
    is_system_cuda_dll = (
        '\\nvidia gpu computing toolkit\\cuda\\' in src_lower or
        '\\nvidia\\cudnn\\' in src_lower or
        ('\\cuda\\v' in src_lower and '\\bin\\' in src_lower)
    )
    is_unwanted_onnx_dll = (
        'onnxruntime_providers_cuda.dll' in name.lower() or
        'directml.dll' in name.lower()
    )

    if not is_system_cuda_dll and not is_unwanted_onnx_dll:
        filtered_binaries.append((name, src, type))
    else:
        reason = "system CUDA DLL" if is_system_cuda_dll else "redundant ONNX DLL"
        print(f"[INFO] Exclude {reason}: {name} (collected from {src})")
a_2.binaries = filtered_binaries


# Exclude modules copied separately as source files.
private_module = ['core', 'config_client', 'config_server', 'LLM', ]

for which in (a_1, a_2):
    filtered = []
    for name, src, type in which.pure:
        if not any(name == m or name.startswith(m + '.') for m in private_module):
            filtered.append((name, src, type))
    which.pure = filtered

# noarchive puts private .pyc modules in datas; exclude them to run source copies.
for which in (a_1, a_2):
    filtered = []
    for name, src, type in which.datas:
        is_private = any(
            name.startswith(m + '/') or name.startswith(m + '\\') or name in (m + '.py', m + '.pyc')
            for m in private_module
        )
        if not is_private:
            filtered.append((name, src, type))
    which.datas = filtered


pyz_1 = PYZ(a_1.pure)
pyz_2 = PYZ(a_2.pure)


exe_1 = EXE(
    pyz_1,
    a_1.scripts,
    [],
    exclude_binaries=True,
    name='start_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\\\server-icon.ico'],
    # Put third-party dependencies in internal.
    contents_directory='internal',
)
exe_2 = EXE(
    pyz_2,
    a_2.scripts,
    [],
    exclude_binaries=True,
    name='start_client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\\\client-icon.ico'],
    # Put third-party dependencies in internal.
    contents_directory='internal',
)

coll = COLLECT(
    exe_1,
    a_1.binaries,
    a_1.datas,

    exe_2,
    a_2.binaries,
    a_2.datas,

    strip=False,
    upx=True,
    upx_exclude=[],
    name='CapsWriter-Offline',
)


# Copy additional project-owned files.
my_files = [
    ('config_templates/config_client_template.py', 'config_client.py'),
    ('config_templates/config_server_template.py', 'config_server.py'),
    ('core_server.py', 'core_server.py'),
    ('core_client.py', 'core_client.py'),
    ('readme.md', 'readme.md'),
    ('LICENSE', 'LICENSE'),
]
my_folders = []     # Directories to copy.
dest_root = join('dist', basename(coll.name))

from build_llm import copy_llm_configuration
copy_llm_configuration('.', dest_root)

# Copy files from the directory.
for folder in my_folders:
    if not exists(folder):
        continue
    for dirpath, dirnames, filenames in walk(folder):
        for filename in filenames:
            src_file = join(dirpath, filename)
            if exists(src_file):
                my_files.append((src_file, src_file))

# Place executables in the package root, outside internal.
for src_file, rel_path in my_files:
    if not exists(src_file):
        continue
    # Preserve relative paths.
    rel_path = rel_path.replace('\\', '/')
    dest_file = join(dest_root, rel_path)
    dest_folder = dirname(dest_file)
    makedirs(dest_folder, exist_ok=True)
    copyfile(src_file, dest_file)


# Link models to avoid copying large files.
from platform import system
from subprocess import run

if system() == 'Windows':
    link_folders = ['models', 'assets', 'core', 'docs', 'log']
    for folder in link_folders:
        if not exists(folder):
            continue
        dest_folder = join(dest_root, folder)
        if exists(dest_folder):
            rmtree(dest_folder)
        # Create the directory junction through Command Prompt.
        cmd = ['mklink', '/j', dest_folder, folder]
        try:
            run(cmd, shell=True, check=True)
        except:
            print(f'Warning: cannot create junction {dest_folder}; create it or copy the directory manually')
