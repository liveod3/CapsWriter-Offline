#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Archive release directories with 7-Zip.

Features:
1. Package CapsWriter-Offline (server and client).
2. Package CapsWriter-Offline-Client (client only).
3. Exclude nested model payloads while retaining top-level model instructions.
"""

import os
import subprocess
from pathlib import Path
from datetime import datetime


def find_7zip():
    """Locate the 7-Zip executable."""
    possible_paths = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]

    # Search PATH.
    for path in os.environ.get("PATH", "").split(os.pathsep):
        possible_paths.append(os.path.join(path, "7z.exe"))

    for path in possible_paths:
        if os.path.exists(path):
            return path

    return None


def should_include_file(file_path, is_client_only=False):
    """
    Return whether a file belongs in the archive.

    Inclusion rules:
    - Include files outside nested model payload directories.
    - Include models/<model>/<file> at depth two.
    - Exclude models/<model>/<subdirectory>/<file> at depth three or more.
    - For the client-only package:
        - Exclude DLLs beneath core because the client does not run local inference.
    """
    path = Path(file_path)
    parts = path.parts

    # 1. Apply client-only exclusions.
    if is_client_only:
        # Exclude DLLs beneath core.
        if 'core' in parts and path.suffix.lower() == '.dll':
            return False

    # 2. Check for the models directory.
    if 'models' not in parts:
        return True  # Include files outside models.

    # Find models in the relative path.
    try:
        models_index = parts.index('models')
    except ValueError:
        return True

    # Exclude model download ZIP archives.
    if 'models' in parts and path.suffix.lower() == '.zip' or  path.suffix.lower() == '.cfg':
        return False

    # Exclude nested model payloads at depth three or more.
    depth = len(parts) - models_index

    if depth >= 4:  # models/<model>/<subdirectory>/<file> or deeper.
        return False
    else:  # models/<model>/<file> or shallower.
        return True


def create_file_list(dist_folder, output_file='file_list.txt', is_client_only=False):
    """
    Build the archive file list.

    7-Zip reads this list through an @ argument.
    Write one path per line relative to the dist directory.
    """
    files = []

    # Traverse dist and collect archive inputs.
    dist_path = Path(dist_folder)
    if not dist_path.exists():
        return files, None

    for root, dirs, filenames in os.walk(dist_path):
        # Exclude unwanted directories.
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '.vscode', '.git')]

        for filename in filenames:
            file_path = os.path.join(root, filename)
            if should_include_file(file_path, is_client_only):
                # Compute paths relative to dist.
                rel_path = os.path.relpath(file_path, dist_path.parent)
                files.append(rel_path)

    if not files:
        return files, None

    # Write the file list.
    list_file = Path(output_file)
    list_file.write_text('\n'.join(files), encoding='utf-8')

    return files, list_file


def package_with_7zip(source_dir, output_zip, file_list_file):
    """Archive a directory with 7-Zip."""

    seven_zip = find_7zip()
    if not seven_zip:
        raise FileNotFoundError(
            "Cannot find 7-Zip. Install it before creating an archive.\n"
            "Download: https://www.7-zip.org/"
        )

    source_path = Path(source_dir)
    if not source_path.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir}")

    # Create the output directory if needed.
    output_path = Path(output_zip)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve the file list.
    # The list is in the working directory; pass its path from dist.
    dist_dir = source_path.parent
    list_file_abs = Path(file_list_file).absolute()
    list_file_rel_to_dist = os.path.relpath(list_file_abs, dist_dir)

    # Build the 7-Zip command.
    # Use -tzip for ZIP output.
    # Use -mx9 for maximum compression.
    # Read archive inputs from @file_list.txt.
    cmd = [
        seven_zip,
        'a',                      # Add files to the archive.
        '-tzip',                  # ZIP format.
        '-mx9',                   # Maximum compression.
        str(output_path.absolute()),  # Absolute output path.
        f'@{list_file_rel_to_dist}',  # Read paths relative to dist from the list.
    ]

    # Read file-list statistics.
    with open(file_list_file, 'r', encoding='utf-8') as f:
        files_count = len(f.readlines())

    print(f"\nPackaging: {source_path.name}")
    print(f"Output archive: {output_zip}")
    print(f"Files to archive: {files_count}")
    print(f"Working directory: {dist_dir.absolute()}")

    # Run compression from dist.
    result = subprocess.run(
        cmd,
        cwd=str(dist_dir),  # Run from dist.
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )

    if result.returncode != 0:
        print(f"\nError: 7-Zip failed")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, cmd)

    print("\n✅ Archive created.")

    # Display archive information.
    info_result = subprocess.run(
        [seven_zip, 'l', str(output_path.absolute())],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )

    if info_result.returncode == 0:
        # Count files and total bytes.
        lines = info_result.stdout.split('\n')
        for line in lines:
            if 'files' in line.lower() or '文件夹' in line or '文件' in line:
                print(f"\nArchive information: {line.strip()}")
                break


def main():
    """Run the entry point."""
    dist_dir = Path('dist')

    # Check dist exists.
    if not dist_dir.exists():
        print(f"Error: dist does not exist")
        print(f"Build with PyInstaller first: pyinstaller build.spec")
        return

    print("=" * 60)
    print("CapsWriter-Offline release archiver")
    print("=" * 60)

    # Create the archive output directory.
    release_dir = Path('release')
    release_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d")

    # Package configurations.
    packages = []

    # Check the combined server/client package.
    server_dist = dist_dir / 'CapsWriter-Offline'
    if server_dist.exists():
        packages.append({
            'source': server_dist,
            'output': release_dir / f'CapsWriter-Offline-{timestamp}.zip',
            'name': 'server and client'
        })

    # Check the client-only package.
    client_dist = dist_dir / 'CapsWriter-Offline-Client'
    if client_dist.exists():
        packages.append({
            'source': client_dist,
            'output': release_dir / f'CapsWriter-Offline-Client-{timestamp}.zip',
            'name': 'client only'
        })

    if not packages:
        print(f"\nError: no build artifacts found in dist")
        print(f"Build with PyInstaller first:")
        print(f"  pyinstaller build.spec")
        print(f"  pyinstaller build-client.spec")
        return

    print(f"\nFound {len(packages)} packages to archive")

    # Archive each package.
    success_count = 0
    for idx, pkg in enumerate(packages):
        try:
            print(f"\n{'=' * 60}")
            print(f"Package: {pkg['name']}")
            print(f"{'=' * 60}")

            # Use a unique file-list name to avoid collisions.
            list_file_name = f'file_list_{idx}.txt'

            # Generate the file list.
            is_client_only = pkg['source'].name == 'CapsWriter-Offline-Client'
            files, list_file = create_file_list(pkg['source'], list_file_name, is_client_only)

            if not files:
                print(f"\nWarning: no files to archive")
                continue

            print(f"File list: {list_file}")

            # Create the archive.
            package_with_7zip(
                pkg['source'],
                pkg['output'],
                list_file
            )

            success_count += 1

            # Remove the temporary file list.
            try:
                list_file.unlink()
                print(f"Removed temporary file list: {list_file}")
            except Exception as cleanup_error:
                print(f"Warning: cannot remove temporary file list {list_file}: {cleanup_error}")

        except Exception as e:
            print(f"\nPackaging failed: {e}")

    # Report results.
    print(f"\n{'=' * 60}")
    print(f"Packaging complete: {success_count}/{len(packages)} succeeded")
    print(f"{'=' * 60}")
    print(f"\nOutput directory: {release_dir.absolute()}")

    # List generated archives.
    if success_count > 0:
        print(f"\nCreated archives:")
        for file in sorted(release_dir.glob('*.zip')):
            size_mb = file.stat().st_size / (1024 * 1024)
            print(f"  {file.name} ({size_mb:.1f} MB)")


if __name__ == '__main__':
    main()
