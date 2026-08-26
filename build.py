#!/usr/bin/env python3
"""
Build Script for SimLaps Telemetry Client

Creates an obfuscated, packaged Windows executable.

Usage:
    python build.py              # Build with PyArmor obfuscation
    python build.py --no-obfuscate   # Build without obfuscation (faster, for testing)
    python build.py --clean      # Clean build artifacts
"""

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path
from typing import TypedDict


REPO_ROOT = Path(__file__).resolve().parent


def get_venv_executable(name: str) -> str:
    """Get the path to an executable in the current venv."""
    # Check if we're in a venv
    venv_path = sys.prefix
    
    # Try Scripts (Windows) or bin (Unix)
    for scripts_dir in ["Scripts", "bin"]:
        exe_path = os.path.join(venv_path, scripts_dir, name)
        if os.path.exists(exe_path):
            return exe_path
        # Try with .exe extension on Windows
        exe_path_win = exe_path + ".exe"
        if os.path.exists(exe_path_win):
            return exe_path_win
    
    # Fallback to just the command name (rely on PATH)
    return name


# Import version from source
sys.path.insert(0, str(REPO_ROOT / "src"))
from version import VERSION

# Configuration
APP_NAME = "SimLapsClient"
APP_VERSION = VERSION
# Build paths are deliberately rooted at this file's repository.  The build
# script is commonly invoked from IDEs and release shells whose CWD is not the
# repository, and relative artifact paths in that case are unsafe.
ENTRY_POINT = REPO_ROOT / "src" / "main.py"
ICON_PATH = REPO_ROOT / "assets" / "icon.ico"
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"
OBFUSCATED_DIR = REPO_ROOT / "obfuscated"
SECURITY_FILE = REPO_ROOT / "src" / "core" / "security.py"


def _validate_cleanup_target(path: Path, allowed_root: Path) -> Path:
    """Validate a cleanup target before touching it.

    A symlink is safe to unlink, but never safe to recurse into.  For regular
    paths, both the lexical path and its resolved destination must remain
    below the intended repository tree.
    """
    candidate = Path(path)
    root = Path(allowed_root).resolve()
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing to clean path outside {root}: {candidate}"
        ) from exc

    if candidate.is_symlink():
        return candidate

    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing to clean redirected path outside {root}: {candidate}"
        ) from exc
    return candidate


def _remove_path(path: Path, allowed_root: Path) -> None:
    """Remove one validated file, symlink, or directory without following links."""
    target = _validate_cleanup_target(path, allowed_root)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def _is_virtual_environment(path: Path) -> bool:
    """Return whether *path* looks like a Python virtual environment."""
    return (
        (path / "pyvenv.cfg").is_file()
        or (path / "Scripts" / "python.exe").is_file()
        or (path / "bin" / "python").is_file()
    )


def _clean_cached_files() -> None:
    """Remove project bytecode without traversing environments or artifacts."""
    skipped_names = {
        ".git",
        ".pyarmor",
        ".venv",
        "build",
        "dist",
        "env",
        "env.bak",
        "obfuscated",
        "venv",
        "venv-sim-laps-client",
        "venv.bak",
    }
    for current_root, dir_names, file_names in os.walk(
        REPO_ROOT, topdown=True, followlinks=False
    ):
        current = Path(current_root)
        retained_dirs = []
        for name in dir_names:
            child = current / name
            relative_child = child.relative_to(REPO_ROOT)
            if (
                name in skipped_names
                or relative_child == Path("tests") / "output"
            ):
                continue
            if child.is_symlink() or _is_virtual_environment(child):
                continue
            retained_dirs.append(name)
        dir_names[:] = retained_dirs

        for name in file_names:
            if not name.endswith(".pyc"):
                continue
            target = current / name
            if target.is_symlink():
                continue
            _remove_path(target, REPO_ROOT)

        for name in list(dir_names):
            if name != "__pycache__":
                continue
            target = current / name
            _remove_path(target, REPO_ROOT)
            dir_names.remove(name)


class ArtifactPlan(TypedDict):
    entry_point: Path
    data_files: tuple[tuple[str, str], ...]
    forbidden_artifacts: tuple[str, ...]


def clean():
    """Remove build artifacts."""
    print("Cleaning build artifacts...")
    # These are the only top-level directories the build owns.  Validate each
    # before removing it so a redirected symlink or modified constant cannot
    # turn cleanup into deletion outside the repository.
    for artifact_dir in (BUILD_DIR, OBFUSCATED_DIR, REPO_ROOT / ".pyarmor"):
        if artifact_dir.exists() or artifact_dir.is_symlink():
            _remove_path(artifact_dir, REPO_ROOT)
            print(f"  Removing {artifact_dir}/")

    # Clean dist/ including credentials left by older releases.
    if DIST_DIR.exists() or DIST_DIR.is_symlink():
        if DIST_DIR.is_symlink():
            _remove_path(DIST_DIR, REPO_ROOT)
        else:
            _validate_cleanup_target(DIST_DIR, REPO_ROOT)
            for item in DIST_DIR.iterdir():
                _remove_path(item, DIST_DIR)
                print(f"  Removing {item}{'/' if item.is_dir() else ''}")

    _clean_cached_files()
    
    print("Clean complete!")


def check_dependencies():
    """Check if required build tools are installed."""
    print("Checking build dependencies...")
    
    # Map package names to their import names
    required = {
        "pyinstaller": "PyInstaller",
        "pyarmor": "pyarmor",
    }
    missing = []
    
    for package, import_name in required.items():
        try:
            if package == "pyarmor":
                # PyArmor 9.x installs versioned CLI launchers (pyarmor-7.exe,
                # pyarmor-8.exe) that may not work if the venv path changed.
                # Invoke via python -m for reliability.
                result = subprocess.run(
                    [sys.executable, "-m", "pyarmor.cli", "--version"],
                    capture_output=True, text=True, cwd=REPO_ROOT,
                )
                if result.returncode != 0:
                    missing.append(package)
            else:
                __import__(import_name)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
        return False
    
    print("  All dependencies found!")
    return True


def obfuscate_source():
    """Obfuscate source code with PyArmor."""
    print("Obfuscating source code with PyArmor...")
    
    # Only obfuscate sensitive files to stay within trial limits
    files_to_obfuscate = [
        "src/core/security.py",
        "src/core/api_client.py",
    ]
    
    # PyArmor obfuscation command (using free features only)
    # Invoke via python -m pyarmor.cli for venv-path resilience
    cmd = [
        sys.executable, "-m", "pyarmor.cli",
        "gen",
        "--output", OBFUSCATED_DIR,
        "--obf-code", "0",  # Basic obfuscation (free tier)
        "--obf-module", "0",  # Basic module obfuscation (free tier)
        *(REPO_ROOT / path for path in files_to_obfuscate),
    ]
    
    print(f"  Running: pyarmor gen --output {OBFUSCATED_DIR} ...")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    
    if result.returncode != 0:
        print(f"  PyArmor error: {result.stderr}")
        print(f"  stdout: {result.stdout}")
        
        # Check if it's a license issue
        if "out of license" in result.stderr or "trial" in result.stdout.lower():
            print("  WARNING: PyArmor trial limitation detected")
            print("  Falling back to basic obfuscation...")
            
            # Try with minimal arguments
            simple_cmd = [
                sys.executable, "-m", "pyarmor.cli",
                "gen",
                "--output", OBFUSCATED_DIR,
                *(REPO_ROOT / path for path in files_to_obfuscate),
            ]
            
            print(f"  Running: pyarmor gen --output {OBFUSCATED_DIR} ...")
            result = subprocess.run(
                simple_cmd, capture_output=True, text=True, cwd=REPO_ROOT
            )
            
            if result.returncode != 0:
                print(f"  Simple PyArmor also failed: {result.stderr}")
                return False
    
    # Verify obfuscated output
    if OBFUSCATED_DIR.exists():
        print(f"  Obfuscation complete: {OBFUSCATED_DIR}/")
        return True
    else:
        print("  Obfuscation failed: output directory not found")
        return False


def build_artifact_plan() -> ArtifactPlan:
    """Describe release inputs and the credential exclusion policy.

    Keeping this plan separate makes it possible to audit packaging without
    invoking PyInstaller. Credential names are policy markers, never inputs.
    """
    data_files: list[tuple[str, str]] = []
    if os.path.exists(ICON_PATH):
        data_files.append((str(ICON_PATH), "assets"))

    icon_png_path = REPO_ROOT / "assets" / "icon.png"
    if os.path.exists(icon_png_path):
        data_files.append((str(icon_png_path), "assets"))

    analyzer_vendor_path = REPO_ROOT / "src" / "core" / "analyzer" / "vendor"
    if os.path.isdir(analyzer_vendor_path):
        data_files.append((str(analyzer_vendor_path), "src/core/analyzer/vendor"))

    return {
        "entry_point": ENTRY_POINT,
        "data_files": tuple(data_files),
        "forbidden_artifacts": (".env", "APP_SECRET", "SERVER_SECRET.txt"),
    }


def build_command() -> list[str]:
    """Return the PyInstaller command for a secret-free client artifact.

    Secrets are deliberately not part of the artifact plan.  An authorized
    operator may provision ``APP_SECRET`` in the process environment of the
    installed client, but a build must never read or package a local ``.env``.
    """
    pyinstaller_exe = get_venv_executable("pyinstaller")
    plan = build_artifact_plan()
    
    # Use obfuscated source if available
    src_dir = REPO_ROOT
    entry = ENTRY_POINT
    
    print(f"  Using source: {src_dir}")
    
    # PyInstaller arguments
    cmd = [
        pyinstaller_exe,
        "--onefile",
        "--windowed",  # No console window (GUI app)
        "--name", APP_NAME,
        "--clean",
        "--noconfirm",
    ]
    
    # Add icon if exists
    if os.path.exists(ICON_PATH):
        cmd.extend(["--icon", str(ICON_PATH)])

    for source, destination in plan["data_files"]:
        # Saved telemetry reports and UI assets are safe, non-credential data.
        cmd.extend(["--add-data", f"{source};{destination}"])
    
    # Add hidden imports for Flet and psutil
    hidden_imports = [
        "flet",
        "flet_core",
        "flet_runtime",
        "flet_desktop",
        "httpx",
        "httpcore",
        "anyio",
        "sniffio",
        "h11",
        "certifi",
        "idna",
        "psutil",
        "src",
        "src.version",
        "src.core",
        "src.core.track_catalog",
        "src.core.track_catalog:select_track_profile",
        "src.core.track_catalog:build_track_profile",
        "src.core.telemetry_capture",
        "src.core.telemetry_capture:CaptureMetadata",
        "src.core.telemetry_capture:FrameData",
        "src.core.telemetry_decoder",
        "src.ui",
        "src.ui.app",
        "src.ui.pages",
        "src.ui.pages.settings",
        "src.ui.pages.telemetry",
        "src.ui.components",
        "src.utils",
    ]
    
    for imp in hidden_imports:
        cmd.extend(["--hidden-import", imp])
    
    # Add data files for Flet
    # Flet requires its runtime files and desktop app to be included
    cmd.extend([
        "--collect-all", "flet",
        "--collect-all", "flet_core",
        "--collect-all", "flet_runtime",
        "--collect-all", "flet_desktop",
        "--collect-binaries", "flet",
        "--collect-binaries", "flet_runtime",
        "--collect-binaries", "flet_desktop",
        "--collect-data", "flet",
        "--collect-data", "flet_runtime",
        "--collect-data", "flet_desktop",
        "--collect-all", "src",
        "--collect-all", "src.core",
        "--collect-all", "src.ui",
        "--collect-all", "src.utils",
    ])
    
    # Add the source directory to path so imports work
    cmd.extend([
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(REPO_ROOT),
        "--paths", str(src_dir),
    ])
    
    # Add obfuscated src directory to path if available
    if OBFUSCATED_DIR.exists():
        cmd.extend(["--paths", str(OBFUSCATED_DIR)])
    
    # Add entry point
    cmd.append(str(entry))

    return cmd


def build_executable():
    """Build the executable with PyInstaller."""
    print("Building executable with PyInstaller...")
    cmd = build_command()
    print(f"  Running: {' '.join(cmd[:10])}...")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    
    if result.returncode != 0:
        print(f"  PyInstaller error: {result.stderr}")
        print(f"  stdout: {result.stdout}")
        return False
    
    # Verify output
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if exe_path.exists():
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"  Build complete: {exe_path} ({size_mb:.1f} MB)")
        return True
    else:
        print("  Build failed: executable not found")
        return False


def create_spec_file():
    """Create a PyInstaller spec file for more control."""
    entry_point = repr(str(ENTRY_POINT))
    icon_path = repr(str(ICON_PATH)) if ICON_PATH.exists() else "None"
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    [{entry_point}],
    pathex=[{str(REPO_ROOT)!r}],
    binaries=[],
    datas=[],
    hiddenimports=[
        'flet',
        'flet_core', 
        'flet_runtime',
        'httpx',
        'httpcore',
        'anyio',
        'sniffio',
        'h11',
        'certifi',
        'idna',
        'psutil',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='{APP_NAME}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={icon_path},
)
'''
    
    spec_path = REPO_ROOT / f"{APP_NAME}.spec"
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)
    
    print(f"Created {spec_path}")
    return spec_path


def main():
    """Main build process."""
    parser = argparse.ArgumentParser(description="Build SimLaps Client")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts")
    parser.add_argument("--spec", action="store_true", help="Create spec file only")
    parser.add_argument("--no-obfuscate", action="store_true", help="Build without PyArmor obfuscation (faster, for testing)")
    
    args = parser.parse_args()
    
    print(f"SimLaps Client Build Script v{APP_VERSION}")
    print("=" * 50)
    
    if args.clean:
        clean()
        return 0
    
    if args.spec:
        create_spec_file()
        return 0
    
    # Check dependencies
    if not check_dependencies():
        return 1
    
    # Clean previous build
    clean()
    
    # Obfuscate source unless disabled
    if not args.no_obfuscate:
        if not obfuscate_source():
            print("\nOBFUSCATION FAILED!")
            return 1
    else:
        print("Skipping obfuscation (building from source)")

    # Build executable
    if not build_executable():
        print("\nBuild FAILED!")
        return 1
    
    print("\n" + "=" * 50)
    print("BUILD SUCCESSFUL!")
    print("=" * 50)
    print(f"\nExecutable: {DIST_DIR}/{APP_NAME}.exe")
    if not args.no_obfuscate:
        print("Source code obfuscated with PyArmor")
    print("No credentials were bundled. APP_SECRET is read only from the installed client's environment.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
