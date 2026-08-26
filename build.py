#!/usr/bin/env python3
"""
Build Script for SimLaps Telemetry Client.

Creates a packaged Windows executable and optionally processes the selected
modules with PyArmor.  PyArmor is a packaging aid, not a protection boundary
for secrets bundled in an executable.

Usage:
    python build.py              # Build with PyArmor obfuscation
    python build.py --no-obfuscate   # Build without obfuscation (faster, for testing)
    python build.py --clean      # Clean build artifacts
    python build.py --secret KEY # Use specific secret (default: generate random)
"""

import os
import sys
import re
import shutil
import secrets
import subprocess
import argparse
from importlib.machinery import PathFinder
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


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
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from version import VERSION

# Configuration
APP_NAME = "SimLapsClient"
APP_VERSION = VERSION
ENTRY_POINT = "src/main.py"  # Correct path to main script
ICON_PATH = "assets/icon.ico"
DIST_DIR = "dist"
BUILD_DIR = "build"
OBFUSCATED_DIR = "obfuscated"
SECURITY_FILE = "src/core/security.py"
OBFUSCATED_MODULES = (
    "src/core/security.py",
    "src/core/api_client.py",
)


def clean():
    """Remove build artifacts."""
    print("Cleaning build artifacts...")
    
    dirs_to_clean = [BUILD_DIR, OBFUSCATED_DIR, "__pycache__", ".pyarmor"]
    
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"  Removing {dir_name}/")
            shutil.rmtree(dir_name)
    
    # Clean dist/
    if os.path.exists(DIST_DIR):
        for item in os.listdir(DIST_DIR):
            item_path = os.path.join(DIST_DIR, item)
            # Preserve old secret file if exists, just in case
            if item != "SERVER_SECRET.txt":
                if os.path.isfile(item_path):
                    os.remove(item_path)
                    print(f"  Removing {item_path}")
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                    print(f"  Removing {item_path}/")
    
    # Remove .pyc files
    for pyc in Path(".").rglob("*.pyc"):
        pyc.unlink()
    
    # Remove __pycache__ directories
    for pycache in Path(".").rglob("__pycache__"):
        shutil.rmtree(pycache)
    
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
                    capture_output=True, text=True,
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

    # Never allow a previous run to make a failed or partial generation look
    # successful.  The output is intentionally isolated from the source tree.
    _clear_obfuscated_output()
    
    # Process only the selected modules to stay within trial limits.
    files_to_obfuscate = list(OBFUSCATED_MODULES)
    
    # PyArmor command (using free features only). A licensed Windows run is
    # still an external smoke test; these checks validate its output contract.
    # Invoke via python -m pyarmor.cli for venv-path resilience
    cmd = [
        sys.executable, "-m", "pyarmor.cli",
        "gen",
        "--output", OBFUSCATED_DIR,
        "--obf-code", "0",  # Basic obfuscation (free tier)
        "--obf-module", "0",  # Basic module obfuscation (free tier)
        *files_to_obfuscate,
    ]
    
    print(f"  Running: pyarmor gen --output {OBFUSCATED_DIR} ...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        print(f"  PyArmor could not be started: {exc}")
        _clear_obfuscated_output()
        return False
    
    if result.returncode != 0:
        print(f"  PyArmor error: {result.stderr}")
        print(f"  stdout: {result.stdout}")
        
        _clear_obfuscated_output()
        return False

    if not _validate_obfuscated_output():
        print("  Obfuscation failed: expected package layout was not generated")
        _clear_obfuscated_output()
        return False

    print(f"  Obfuscation complete: {OBFUSCATED_DIR}/src/core/")
    return True


def _clear_obfuscated_output() -> None:
    """Remove the isolated PyArmor output directory, if present."""
    output = Path(OBFUSCATED_DIR)
    if output.is_symlink() or output.is_file():
        output.unlink()
    elif output.is_dir():
        shutil.rmtree(output)


def _validate_obfuscated_output() -> bool:
    """Validate that PyArmor generated the exact importable module paths.

    PyArmor can emit a flat tree when individual files are passed to ``gen``.
    The build accepts that form only after explicitly staging both generated
    replacements into the package-preserving ``src/core`` tree.  Any other
    top-level layout is rejected so PyInstaller cannot silently package the
    plain source modules instead.
    """
    output = Path(OBFUSCATED_DIR)
    if not output.is_dir():
        return False

    expected = [output / relative for relative in OBFUSCATED_MODULES]
    if not all(path.is_file() for path in expected):
        flat = [output / Path(relative).name for relative in OBFUSCATED_MODULES]
        if not all(path.is_file() for path in flat):
            return False
        package_dir = output / "src" / "core"
        package_dir.mkdir(parents=True, exist_ok=True)
        for source, destination in zip(flat, expected):
            shutil.move(str(source), str(destination))

    return all(
        (output / relative).is_file()
        and (output / relative).stat().st_size > 0
        for relative in OBFUSCATED_MODULES
    )


def _validate_runtime_package(package_root: Path) -> bool:
    """Ensure the staged package resolves each replacement by its real name."""
    src_root = package_root / "src"
    core_root = src_root / "core"
    src_spec = PathFinder.find_spec("src", [str(package_root)])
    core_spec = PathFinder.find_spec("src.core", [str(src_root)])
    if not src_spec or not core_spec:
        return False
    for relative in OBFUSCATED_MODULES:
        module_name = ".".join(Path(relative).with_suffix("").parts)
        spec = PathFinder.find_spec(module_name, [str(core_root)])
        expected = (package_root / relative).resolve()
        if not spec or not spec.origin or Path(spec.origin).resolve() != expected:
            return False
    return True


def _stage_obfuscated_source() -> str:
    """Create a complete package tree with validated replacements staged in."""
    output = Path(OBFUSCATED_DIR)
    package_root = output / "src"
    generated = {
        relative: output / relative for relative in OBFUSCATED_MODULES
    }
    generated_bytes = {
        relative: source.read_bytes() for relative, source in generated.items()
    }
    shutil.copytree(PROJECT_ROOT / "src", package_root, dirs_exist_ok=True)
    for relative, contents in generated_bytes.items():
        destination = package_root / Path(relative).relative_to("src")
        destination.write_bytes(contents)
    if not _validate_runtime_package(output):
        raise RuntimeError("staged obfuscated modules do not resolve from src.core")
    return str(output)


def build_executable(use_obfuscated: bool = False):
    """Build the executable with PyInstaller."""
    print("Building executable with PyInstaller...")
    
    pyinstaller_exe = get_venv_executable("pyinstaller")
    
    # Select the source tree explicitly.  Do not infer this from whether a
    # stale ``obfuscated/`` directory happens to exist.
    if use_obfuscated:
        if not _validate_obfuscated_output():
            print("  Build refused: obfuscated output is missing or invalid")
            return False
        try:
            src_dir = _stage_obfuscated_source()
        except (OSError, RuntimeError) as exc:
            print(f"  Build refused: {exc}")
            return False
        entry = os.path.join(src_dir, ENTRY_POINT)
    else:
        src_dir = "."
        entry = ENTRY_POINT
        _clear_obfuscated_output()
    
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
        cmd.extend(["--icon", ICON_PATH])
        # Include icon.ico as data file for window icon at runtime
        cmd.extend(["--add-data", f"{ICON_PATH};assets"])
    
    # Also include icon.png for ft.Image in the UI
    icon_png_path = "assets/icon.png"
    if os.path.exists(icon_png_path):
        cmd.extend(["--add-data", f"{icon_png_path};assets"])

    # Saved telemetry reports are self-contained and load these pinned chart
    # libraries from the frozen application's extraction directory.
    analyzer_vendor_path = "src/core/analyzer/vendor"
    if os.path.isdir(analyzer_vendor_path):
        cmd.extend([
            "--add-data",
            f"{analyzer_vendor_path};src/core/analyzer/vendor",
        ])
    
    # Include .env file for runtime secret loading
    if os.path.exists(".env"):
        cmd.extend(["--add-data", ".env;."])
        print("  Including .env file in build")
    else:
        print("  WARNING: .env file not found - build may fail at runtime")
        print("  Create .env file with APP_SECRET before building")
    
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
    cmd.extend(["--paths", src_dir])
    
    # Add entry point
    cmd.append(entry)
    
    print(f"  Running: {' '.join(cmd[:10])}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"  PyInstaller error: {result.stderr}")
        print(f"  stdout: {result.stdout}")
        return False
    
    # Verify output
    exe_path = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"  Build complete: {exe_path} ({size_mb:.1f} MB)")
        return True
    else:
        print("  Build failed: executable not found")
        return False


def create_spec_file():
    """Create a PyInstaller spec file for more control."""
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['src/main.py'],
    pathex=[],
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
    icon='assets/icon.ico' if os.path.exists('assets/icon.ico') else None,
)
'''
    
    spec_path = f"{APP_NAME}.spec"
    with open(spec_path, "w") as f:
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
    
    # Check that .env file exists
    if not os.path.exists(".env"):
        print("\nERROR: .env file not found!")
        print("Please create .env from .env.example:")
        print("  copy .env.example .env")
        print("\nThe .env file must contain APP_SECRET for signing lap submissions.")
        return 1
    
    print("Using APP_SECRET from .env file")
    
    # Clean previous build
    clean()
    
    # Obfuscate source unless disabled
    if not args.no_obfuscate:
        if not obfuscate_source():
            print("\nOBFUSCATION FAILED!")
            return 1
    else:
        # ``clean`` normally removes this directory, but keep this invariant
        # local to the mode switch so callers/tests cannot accidentally reuse
        # stale generated code.
        _clear_obfuscated_output()
        print("Skipping obfuscation (building from source)")

    # Build executable
    if not build_executable(use_obfuscated=not args.no_obfuscate):
        print("\nBuild FAILED!")
        return 1
    
    print("\n" + "=" * 50)
    print("BUILD SUCCESSFUL!")
    print("=" * 50)
    print(f"\nExecutable: {DIST_DIR}/{APP_NAME}.exe")
    if not args.no_obfuscate:
        print("Source code obfuscated with PyArmor")
    print("Server secret loaded from .env file (bundled in executable)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
