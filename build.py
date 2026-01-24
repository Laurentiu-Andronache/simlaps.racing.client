#!/usr/bin/env python3
"""
Build Script for SimLaps Telemetry Client

Creates an obfuscated, packaged Windows executable with embedded secret.

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
from pathlib import Path


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
ENTRY_POINT = "src/main.py"
ICON_PATH = "assets/icon.ico"
DIST_DIR = "dist"
BUILD_DIR = "build"
OBFUSCATED_DIR = "obfuscated"
SECURITY_FILE = "src/core/security.py"
SECRET_OUTPUT_FILE = "dist/SERVER_SECRET.txt"

# XOR key for basic obfuscation (must match security.py)
XOR_KEY = 0x5A


def generate_secret(length: int = 32) -> bytes:
    """Generate a cryptographically secure random secret."""
    return secrets.token_bytes(length)


def encode_secret(secret: bytes) -> str:
    """XOR-encode the secret and return as hex string."""
    encoded = bytes([b ^ XOR_KEY for b in secret])
    return encoded.hex()


def inject_secret_into_source(secret: bytes) -> bool:
    """
    Inject the encoded secret into the security.py file.
    
    Returns True if successful.
    """
    print("Injecting secret into source code...")
    
    security_path = Path(SECURITY_FILE)
    if not security_path.exists():
        print(f"  Error: {SECURITY_FILE} not found")
        return False
    
    # Read the file
    content = security_path.read_text(encoding="utf-8")
    
    # Encode the secret
    encoded = encode_secret(secret)
    
    # Replace the placeholder
    pattern = r'_ENCODED_SECRET = "[^"]*"'
    replacement = f'_ENCODED_SECRET = "{encoded}"'
    
    new_content, count = re.subn(pattern, replacement, content)
    
    if count == 0:
        print("  Error: Could not find _ENCODED_SECRET in security.py")
        return False
    
    # Write back
    security_path.write_text(new_content, encoding="utf-8")
    print(f"  Injected {len(secret)}-byte secret (XOR-encoded)")
    
    return True


def restore_placeholder_secret() -> None:
    """Restore the placeholder secret in security.py (for git cleanliness)."""
    security_path = Path(SECURITY_FILE)
    if not security_path.exists():
        return
    
    content = security_path.read_text(encoding="utf-8")
    
    pattern = r'_ENCODED_SECRET = "[^"]*"'
    replacement = '_ENCODED_SECRET = "PLACEHOLDER_SECRET_WILL_BE_REPLACED_AT_BUILD_TIME"'
    
    new_content = re.sub(pattern, replacement, content)
    security_path.write_text(new_content, encoding="utf-8")


def save_server_secret(secret: bytes) -> None:
    """Save the raw secret for server configuration."""
    os.makedirs(DIST_DIR, exist_ok=True)
    
    with open(SECRET_OUTPUT_FILE, "w") as f:
        f.write("# SimLaps Client Secret\n")
        f.write("# Add this to your server's .env file\n")
        f.write("#\n")
        f.write(f"CLIENT_APP_SECRET={secret.hex()}\n")
    
    print(f"  Server secret saved to: {SECRET_OUTPUT_FILE}")


def clean():
    """Remove build artifacts (preserves SERVER_SECRET.txt)."""
    print("Cleaning build artifacts...")
    
    dirs_to_clean = [BUILD_DIR, OBFUSCATED_DIR, "__pycache__", ".pyarmor"]
    
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"  Removing {dir_name}/")
            shutil.rmtree(dir_name)
    
    # Clean dist/ but preserve SERVER_SECRET.txt
    if os.path.exists(DIST_DIR):
        for item in os.listdir(DIST_DIR):
            item_path = os.path.join(DIST_DIR, item)
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
    
    # Restore placeholder in security.py
    restore_placeholder_secret()
    
    print("Clean complete!")


def check_dependencies():
    """Check if required build tools are installed."""
    print("Checking build dependencies...")
    
    # Map package names to their import names
    required = {
        "pyinstaller": "PyInstaller",
    }
    missing = []
    
    for package, import_name in required.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
        return False
    
    print("  All dependencies found!")
    return True


def obfuscate_with_pyarmor():
    """Obfuscate source code with PyArmor."""
    print("Obfuscating source code with PyArmor...")
    
    pyarmor_exe = get_venv_executable("pyarmor")
    
    # Check if pyarmor is available
    try:
        result = subprocess.run(
            [pyarmor_exe, "--version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("  Warning: PyArmor not available, skipping obfuscation")
            return False
    except FileNotFoundError:
        print("  Warning: PyArmor not installed, skipping obfuscation")
        return False
    
    # Create obfuscated directory
    os.makedirs(OBFUSCATED_DIR, exist_ok=True)
    
    # Run PyArmor to obfuscate
    cmd = [
        pyarmor_exe, "gen",
        "--output", OBFUSCATED_DIR,
        "--recursive",
        "src",
    ]
    
    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"  PyArmor error: {result.stderr}")
        return False
    
    print("  Obfuscation complete!")
    return True


def build_executable(use_obfuscated: bool = True):
    """Build the executable with PyInstaller."""
    print("Building executable with PyInstaller...")
    
    pyinstaller_exe = get_venv_executable("pyinstaller")
    
    # Determine source directory
    if use_obfuscated and os.path.exists(OBFUSCATED_DIR):
        src_dir = OBFUSCATED_DIR
        entry = os.path.join(OBFUSCATED_DIR, "src", "main.py")
    else:
        src_dir = "."
        entry = ENTRY_POINT
    
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
        "src.ui",
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
    ])
    
    # Add the src directory as a path so imports work
    cmd.extend(["--paths", "."])
    
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
    parser.add_argument("--no-obfuscate", action="store_true", help="Skip obfuscation")
    parser.add_argument("--spec", action="store_true", help="Create spec file only")
    parser.add_argument("--secret", type=str, help="Use specific secret (hex string)")
    
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
    
    # Generate or use provided secret
    if args.secret:
        try:
            secret = bytes.fromhex(args.secret)
            print(f"Using provided secret ({len(secret)} bytes)")
        except ValueError:
            print("Error: --secret must be a valid hex string")
            return 1
    else:
        secret = generate_secret(32)
        print(f"Generated new secret ({len(secret)} bytes)")
    
    # Inject secret into source
    if not inject_secret_into_source(secret):
        print("\nBuild FAILED! Could not inject secret.")
        restore_placeholder_secret()
        return 1
    
    try:
        # Obfuscate (optional)
        use_obfuscated = False
        if not args.no_obfuscate:
            use_obfuscated = obfuscate_with_pyarmor()
        
        # Build executable
        if not build_executable(use_obfuscated):
            print("\nBuild FAILED!")
            return 1
        
        # Save server secret
        save_server_secret(secret)
        
        print("\n" + "=" * 50)
        print("BUILD SUCCESSFUL!")
        print("=" * 50)
        print(f"\nExecutable: {DIST_DIR}/{APP_NAME}.exe")
        print(f"Server secret: {SECRET_OUTPUT_FILE}")
        print("\nIMPORTANT: Add the secret from SERVER_SECRET.txt to your")
        print("server's .env file as CLIENT_APP_SECRET")
        
        return 0
        
    finally:
        # Always restore placeholder (keep source clean)
        restore_placeholder_secret()


if __name__ == "__main__":
    sys.exit(main())
