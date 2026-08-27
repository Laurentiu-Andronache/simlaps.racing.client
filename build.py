#!/usr/bin/env python3
"""
Build Script for SimLaps Telemetry Client

Creates an obfuscated, packaged Windows executable with embedded secret.

Usage:
    python build.py              # Build with PyArmor obfuscation
    python build.py --no-obfuscate   # Build without obfuscation (faster, for testing)
    python build.py --clean      # Clean build artifacts
"""

import os
import sys
import shutil
import secrets
import subprocess
import argparse
from pathlib import Path

from dotenv import dotenv_values


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

# Build-time embedded secret: generated Cython module compiled to a native
# extension so the release artifact carries no plaintext credential.
SECRET_STAGE_DIR = os.path.join(BUILD_DIR, "secret_stage")
EMBEDDED_SECRET_MODULE = "_embedded_secret"
PLACEHOLDER_SECRETS = frozenset({"blahtopsecret"})


def clean():
    """Remove build artifacts."""
    print("Cleaning build artifacts...")
    
    dirs_to_clean = [BUILD_DIR, "__pycache__", ".pyarmor"]
    
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"  Removing {dir_name}/")
            shutil.rmtree(dir_name)
    
    # Clean dist/, including credential artifacts left by older releases.
    if os.path.exists(DIST_DIR):
        for item in os.listdir(DIST_DIR):
            item_path = os.path.join(DIST_DIR, item)
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
        "cython": "Cython",
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
    
    # Only obfuscate sensitive files to stay within trial limits
    files_to_obfuscate = [
        "src/core/security.py",
        "src/core/api_client.py",
    ]
    
    # PyArmor obfuscation command (free tier maximum: code-object and
    # module-level obfuscation, which are the defaults at level 1).
    # Invoke via python -m pyarmor.cli for venv-path resilience.
    cmd = [
        sys.executable, "-m", "pyarmor.cli",
        "gen",
        "--output", OBFUSCATED_DIR,
        "--obf-code", "1",  # Obfuscate each function code object (free tier)
        "--obf-module", "1",  # Obfuscate whole module code (free tier)
        *files_to_obfuscate,
    ]
    
    print(f"  Running: pyarmor gen --output {OBFUSCATED_DIR} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
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
                *files_to_obfuscate,
            ]
            
            print(f"  Running: pyarmor gen --output {OBFUSCATED_DIR} ...")
            result = subprocess.run(simple_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"  Simple PyArmor also failed: {result.stderr}")
                return False
    
    # Verify obfuscated output
    if os.path.exists(OBFUSCATED_DIR):
        print(f"  Obfuscation complete: {OBFUSCATED_DIR}/")
        return True
    else:
        print("  Obfuscation failed: output directory not found")
        return False


def get_build_secret() -> str | None:
    """Resolve the APP_SECRET to embed, from the process env or a local .env.

    Returns None when no usable secret is available (unset or placeholder).
    """
    secret = os.environ.get("APP_SECRET")
    if not secret and os.path.exists(".env"):
        secret = dotenv_values(".env").get("APP_SECRET")
    if not secret or secret.strip() in PLACEHOLDER_SECRETS:
        return None
    return secret.strip()


def generate_secret_module_source(secret: str) -> str:
    """Generate Cython source carrying the secret as two XOR pads.

    The secret never appears as a contiguous literal; it is reconstructed at
    runtime as pad_a ^ pad_b inside compiled machine code.
    """
    data = secret.encode("utf-8")
    pad_a = secrets.token_bytes(len(data))
    pad_b = bytes(a ^ b for a, b in zip(pad_a, data))

    def fmt(raw: bytes) -> str:
        return "".join(f"\\x{byte:02x}" for byte in raw)

    return (
        "# Auto-generated at build time by build.py. Never commit this file.\n"
        f'_PA = b"{fmt(pad_a)}"\n'
        f'_PB = b"{fmt(pad_b)}"\n'
        "\n"
        "def get_secret() -> bytes:\n"
        "    return bytes(a ^ b for a, b in zip(_PA, _PB))\n"
    )


def stage_embedded_secret() -> bool:
    """Generate and compile the embedded-secret native extension.

    Output lands in SECRET_STAGE_DIR (inside build/, which is gitignored) and
    is picked up by PyInstaller via --paths + --hidden-import.
    """
    print("Embedding APP_SECRET as compiled native module...")

    secret = get_build_secret()
    if not secret:
        print("\nERROR: no usable APP_SECRET found in the process environment or .env!")
        print("Set APP_SECRET before building (see .env.example).")
        print("A release without the embedded secret cannot submit laps.")
        return False

    os.makedirs(SECRET_STAGE_DIR, exist_ok=True)

    pyx_path = os.path.join(SECRET_STAGE_DIR, f"{EMBEDDED_SECRET_MODULE}.pyx")
    with open(pyx_path, "w", encoding="utf-8") as f:
        f.write(generate_secret_module_source(secret))

    setup_path = os.path.join(SECRET_STAGE_DIR, "setup.py")
    with open(setup_path, "w", encoding="utf-8") as f:
        f.write(
            "from setuptools import Extension, setup\n"
            "from Cython.Build import cythonize\n"
            "setup(ext_modules=cythonize(\n"
            f'    [Extension("{EMBEDDED_SECRET_MODULE}", ["{EMBEDDED_SECRET_MODULE}.pyx"])],\n'
            "    language_level=3,\n"
            "))\n"
        )

    result = subprocess.run(
        [sys.executable, "setup.py", "build_ext", "--inplace"],
        cwd=SECRET_STAGE_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Cython compile failed: {result.stderr}")
        print("  Ensure Cython is installed and MSVC Build Tools are present.")
        return False

    compiled = list(Path(SECRET_STAGE_DIR).glob(f"{EMBEDDED_SECRET_MODULE}.*.pyd"))
    if not compiled:
        print("  Cython compile produced no .pyd output")
        return False

    print(f"  Embedded secret module: {compiled[0]}")
    return True


def build_executable():
    """Build the executable with PyInstaller."""
    print("Building executable with PyInstaller...")
    
    pyinstaller_exe = get_venv_executable("pyinstaller")
    
    # Use obfuscated source if available
    src_dir = "."  # Always use root since main.py is at root
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
    
    # Never bundle .env as data: the secret ships only inside the compiled
    # native extension produced by stage_embedded_secret().
    if os.path.isdir(SECRET_STAGE_DIR):
        if not list(Path(SECRET_STAGE_DIR).glob(f"{EMBEDDED_SECRET_MODULE}.*.pyd")):
            print("  ERROR: embedded secret module not staged - run stage_embedded_secret first")
            return False
        cmd.extend(["--paths", SECRET_STAGE_DIR])
        hidden_imports_extra = [EMBEDDED_SECRET_MODULE]
    else:
        print("  WARNING: no embedded secret module - release will run in offline mode")
        hidden_imports_extra = []
    
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
    
    for imp in [*hidden_imports, *hidden_imports_extra]:
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
    
    # Add obfuscated src directory first so it shadows the plain sources
    if os.path.exists(OBFUSCATED_DIR):
        cmd.extend(["--paths", os.path.join(OBFUSCATED_DIR)])
    
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
    
    # Clean previous build (also wipes the previous secret staging dir)
    clean()
    
    # Compile the embedded secret module from APP_SECRET (env or local .env)
    if not stage_embedded_secret():
        return 1
    
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
    print("APP_SECRET embedded as compiled native module (no plaintext .env in artifact)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
