#!/usr/bin/env python3
"""Build the SimLaps Client without embedding release credentials."""

import argparse
import os
import shutil
import subprocess
import sys
from importlib.machinery import PathFinder
from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = REPO_ROOT
sys.path.insert(0, str(REPO_ROOT / "src"))
from version import VERSION

APP_NAME = "SimLapsClient"
APP_VERSION = VERSION
ENTRY_POINT = REPO_ROOT / "src" / "main.py"
ICON_PATH = REPO_ROOT / "assets" / "icon.ico"
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"
OBFUSCATED_DIR = REPO_ROOT / "obfuscated"
SECURITY_FILE = REPO_ROOT / "src" / "core" / "security.py"
OBFUSCATED_MODULES = ("src/core/security.py", "src/core/api_client.py")


class ArtifactPlan(TypedDict):
    entry_point: Path
    data_files: tuple[tuple[str, str], ...]
    forbidden_artifacts: tuple[str, ...]


def get_venv_executable(name: str) -> str:
    for scripts_dir in ("Scripts", "bin"):
        exe_path = Path(sys.prefix) / scripts_dir / name
        if exe_path.exists():
            return str(exe_path)
        if Path(f"{exe_path}.exe").exists():
            return f"{exe_path}.exe"
    return name


def _validate_cleanup_target(path: Path, allowed_root: Path) -> Path:
    candidate = Path(path)
    root = Path(allowed_root).resolve()
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to clean path outside {root}: {candidate}") from exc
    if candidate.is_symlink():
        return candidate
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to clean redirected path outside {root}: {candidate}") from exc
    return candidate


def _remove_path(path: Path, allowed_root: Path) -> None:
    target = _validate_cleanup_target(path, allowed_root)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def _is_virtual_environment(path: Path) -> bool:
    return ((path / "pyvenv.cfg").is_file() or
            (path / "Scripts" / "python.exe").is_file() or
            (path / "bin" / "python").is_file())


def _clean_cached_files() -> None:
    skipped_names = {".git", ".pyarmor", ".venv", "build", "dist", "env", "env.bak",
                     "obfuscated", "venv", "venv-sim-laps-client", "venv.bak"}
    for current_root, dir_names, file_names in os.walk(REPO_ROOT, topdown=True, followlinks=False):
        current = Path(current_root)
        kept = []
        for name in dir_names:
            child = current / name
            relative = child.relative_to(REPO_ROOT)
            if name in skipped_names or relative == Path("tests") / "output":
                continue
            if child.is_symlink() or _is_virtual_environment(child):
                continue
            kept.append(name)
        dir_names[:] = kept
        for name in file_names:
            if name.endswith(".pyc") and not (current / name).is_symlink():
                _remove_path(current / name, REPO_ROOT)
        for name in list(dir_names):
            if name == "__pycache__":
                _remove_path(current / name, REPO_ROOT)
                dir_names.remove(name)


def _cleanup_root(path: Path) -> Path:
    return REPO_ROOT if Path(path).is_absolute() else Path.cwd().resolve()


def clean() -> None:
    print("Cleaning build artifacts...")
    for artifact_dir in (BUILD_DIR, OBFUSCATED_DIR, REPO_ROOT / ".pyarmor"):
        artifact_path = Path(artifact_dir)
        if artifact_path.exists() or artifact_path.is_symlink():
            _remove_path(artifact_path, _cleanup_root(artifact_path))
            print(f"  Removing {artifact_path}/")
    dist_path = Path(DIST_DIR)
    if dist_path.exists() or dist_path.is_symlink():
        if dist_path.is_symlink():
            _remove_path(dist_path, _cleanup_root(dist_path))
        else:
            dist_root = _cleanup_root(dist_path)
            _validate_cleanup_target(dist_path, dist_root)
            for item in dist_path.iterdir():
                _remove_path(item, dist_path.resolve())
                print(f"  Removing {item}{'/' if item.is_dir() else ''}")
    _clean_cached_files()
    print("Clean complete!")


def check_dependencies() -> bool:
    print("Checking build dependencies...")
    missing = []
    try:
        __import__("PyInstaller")
    except ImportError:
        missing.append("pyinstaller")
    result = subprocess.run([sys.executable, "-m", "pyarmor.cli", "--version"],
                            capture_output=True, text=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        missing.append("pyarmor")
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
        return False
    print("  All dependencies found!")
    return True


def _clear_obfuscated_output() -> None:
    output = Path(OBFUSCATED_DIR)
    if output.is_symlink() or output.is_file():
        output.unlink()
    elif output.is_dir():
        shutil.rmtree(output)


def _validate_obfuscated_output() -> bool:
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
    return all((output / relative).is_file() and (output / relative).stat().st_size > 0
               for relative in OBFUSCATED_MODULES)


def _validate_runtime_package(package_root: Path) -> bool:
    src_root = package_root / "src"
    core_root = src_root / "core"
    if not PathFinder.find_spec("src", [str(package_root)]) or not PathFinder.find_spec("src.core", [str(src_root)]):
        return False
    for relative in OBFUSCATED_MODULES:
        module_name = ".".join(Path(relative).with_suffix("").parts)
        spec = PathFinder.find_spec(module_name, [str(core_root)])
        expected = (package_root / relative).resolve()
        if not spec or not spec.origin or Path(spec.origin).resolve() != expected:
            return False
    return True


def _stage_obfuscated_source() -> str:
    output = Path(OBFUSCATED_DIR)
    package_root = output / "src"
    generated = {relative: output / relative for relative in OBFUSCATED_MODULES}
    contents = {relative: path.read_bytes() for relative, path in generated.items()}
    shutil.copytree(PROJECT_ROOT / "src", package_root, dirs_exist_ok=True)
    for relative, data in contents.items():
        (package_root / Path(relative).relative_to("src")).write_bytes(data)
    if not _validate_runtime_package(output):
        raise RuntimeError("staged obfuscated modules do not resolve from src.core")
    return str(output)


def obfuscate_source() -> bool:
    print("Obfuscating source code with PyArmor...")
    _clear_obfuscated_output()
    cmd = [sys.executable, "-m", "pyarmor.cli", "gen", "--output", str(OBFUSCATED_DIR),
           "--obf-code", "0", "--obf-module", "0",
           *(REPO_ROOT / path for path in OBFUSCATED_MODULES)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    except OSError as exc:
        print(f"  PyArmor could not be started: {exc}")
        _clear_obfuscated_output()
        return False
    if result.returncode != 0:
        print(f"  PyArmor error: {result.stderr}")
        _clear_obfuscated_output()
        return False
    if not _validate_obfuscated_output():
        print("  Obfuscation failed: expected package layout was not generated")
        _clear_obfuscated_output()
        return False
    print(f"  Obfuscation complete: {OBFUSCATED_DIR}/src/core/")
    return True


def build_artifact_plan() -> ArtifactPlan:
    data_files: list[tuple[str, str]] = []
    if ICON_PATH.exists():
        data_files.append((str(ICON_PATH), "assets"))
    icon_png = REPO_ROOT / "assets" / "icon.png"
    if icon_png.exists():
        data_files.append((str(icon_png), "assets"))
    vendor = REPO_ROOT / "src" / "core" / "analyzer" / "vendor"
    if vendor.is_dir():
        data_files.append((str(vendor), "src/core/analyzer/vendor"))
    return {"entry_point": ENTRY_POINT, "data_files": tuple(data_files),
            "forbidden_artifacts": (".env", "APP_SECRET", "SERVER_SECRET.txt")}


def build_command(use_obfuscated: bool = False) -> list[str]:
    plan = build_artifact_plan()
    if use_obfuscated:
        if not _validate_obfuscated_output():
            raise RuntimeError("obfuscated output is missing or invalid")
        src_dir = _stage_obfuscated_source()
        entry = Path(src_dir) / ENTRY_POINT.relative_to(REPO_ROOT)
    else:
        _clear_obfuscated_output()
        src_dir = str(REPO_ROOT)
        entry = ENTRY_POINT
    cmd = [get_venv_executable("pyinstaller"), "--onefile", "--windowed", "--name", APP_NAME,
           "--clean", "--noconfirm"]
    if ICON_PATH.exists():
        cmd.extend(["--icon", str(ICON_PATH)])
    for source, destination in plan["data_files"]:
        cmd.extend(["--add-data", f"{source};{destination}"])
    for imp in ("flet", "flet_core", "flet_runtime", "flet_desktop", "httpx", "httpcore",
                "anyio", "sniffio", "h11", "certifi", "idna", "psutil", "src",
                "src.version", "src.core", "src.core.track_catalog", "src.ui", "src.ui.app",
                "src.ui.pages", "src.ui.pages.settings", "src.ui.pages.telemetry", "src.utils"):
        cmd.extend(["--hidden-import", imp])
    cmd.extend(["--collect-all", "flet", "--collect-all", "flet_core", "--collect-all", "flet_runtime",
                "--collect-all", "flet_desktop", "--collect-binaries", "flet",
                "--collect-binaries", "flet_runtime", "--collect-binaries", "flet_desktop",
                "--collect-data", "flet", "--collect-data", "flet_runtime", "--collect-data", "flet_desktop",
                "--collect-all", "src", "--collect-all", "src.core", "--collect-all", "src.ui",
                "--collect-all", "src.utils", "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR),
                "--specpath", str(REPO_ROOT), "--paths", str(src_dir), str(entry)])
    return cmd


def build_executable(use_obfuscated: bool = False) -> bool:
    print("Building executable with PyInstaller...")
    try:
        cmd = build_command(use_obfuscated=use_obfuscated)
    except (OSError, RuntimeError) as exc:
        print(f"  Build refused: {exc}")
        return False
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(f"  PyInstaller error: {result.stderr}")
        return False
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if exe_path.exists():
        print(f"  Build complete: {exe_path} ({exe_path.stat().st_size / (1024 * 1024):.1f} MB)")
        return True
    print("  Build failed: executable not found")
    return False


def create_spec_file() -> Path:
    """Create a rooted PyInstaller spec file for manual inspection."""
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
        'flet', 'flet_core', 'flet_runtime', 'httpx', 'httpcore',
        'anyio', 'sniffio', 'h11', 'certifi', 'idna', 'psutil',
    ],
    hookspath=[], hooksconfig={{}}, runtime_hooks=[], excludes=[],
    win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='{APP_NAME}', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    icon={icon_path},
)
'''
    spec_path = REPO_ROOT / f"{APP_NAME}.spec"
    spec_path.write_text(spec_content, encoding="utf-8")
    print(f"Created {spec_path}")
    return spec_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SimLaps Client")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--spec", action="store_true")
    parser.add_argument("--no-obfuscate", action="store_true")
    args = parser.parse_args()
    print(f"SimLaps Client Build Script v{APP_VERSION}\n" + "=" * 50)
    if args.clean:
        clean()
        return 0
    if args.spec:
        create_spec_file()
        return 0
    if not check_dependencies():
        return 1
    clean()
    if args.no_obfuscate:
        _clear_obfuscated_output()
        print("Skipping obfuscation (building from source)")
        build_ok = build_executable()
    elif not obfuscate_source():
        print("\nOBFUSCATION FAILED!")
        return 1
    else:
        build_ok = build_executable(use_obfuscated=True)
    if not build_ok:
        print("\nBuild FAILED!")
        return 1
    print("\nBUILD SUCCESSFUL!\nNo credentials were bundled. APP_SECRET is read only from the installed client's environment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
