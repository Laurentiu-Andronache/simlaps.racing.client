"""Regression tests for rooted builds and isolated PyArmor output."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import build

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "build.py"


@pytest.fixture
def build_module():
    spec = importlib.util.spec_from_file_location("simlaps_build", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_paths_are_rooted_at_build_script(build_module):
    assert build_module.REPO_ROOT == REPO_ROOT
    for path in (build_module.ENTRY_POINT, build_module.ICON_PATH, build_module.DIST_DIR,
                 build_module.BUILD_DIR, build_module.OBFUSCATED_DIR, build_module.SECURITY_FILE):
        assert Path(path).is_absolute()
        assert Path(path).is_relative_to(REPO_ROOT)


def test_clean_from_foreign_cwd_does_not_touch_caller_artifacts():
    # Running the script from another directory must clean only its repository.
    import tempfile
    with tempfile.TemporaryDirectory() as raw:
        caller_root = Path(raw) / "caller"
        caller_root.mkdir()
        (caller_root / "build").mkdir()
        (caller_root / "dist").mkdir()
        build_sentinel = caller_root / "build" / "do-not-delete.txt"
        dist_sentinel = caller_root / "dist" / "do-not-delete.txt"
        build_sentinel.write_text("keep", encoding="utf-8")
        dist_sentinel.write_text("keep", encoding="utf-8")
        result = subprocess.run([sys.executable, str(BUILD_SCRIPT), "--clean"], cwd=caller_root,
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert build_sentinel.exists() and dist_sentinel.exists()


def test_build_command_uses_repository_cwd_and_absolute_paths(tmp_path, build_module, monkeypatch):
    calls = []
    class FailedBuild:
        returncode = 1
        stderr = "expected test failure"
        stdout = ""
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return FailedBuild()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_module, "get_venv_executable", lambda _: "pyinstaller")
    monkeypatch.setattr(build_module.subprocess, "run", fake_run)
    assert build_module.build_executable() is False
    command, kwargs = calls[0]
    assert kwargs["cwd"] == build_module.REPO_ROOT
    assert str(build_module.ENTRY_POINT) in command
    assert str(build_module.DIST_DIR) in command
    assert str(build_module.BUILD_DIR) in command


def test_clean_unlinks_redirected_artifact_without_deleting_target(tmp_path, build_module, monkeypatch):
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    local_venv_cache = fake_repo / "custom-local-env" / "Lib" / "__pycache__"
    local_venv_cache.mkdir(parents=True)
    (fake_repo / "custom-local-env" / "pyvenv.cfg").write_text("home = python", encoding="utf-8")
    venv_bytecode = local_venv_cache / "keep.pyc"
    venv_bytecode.write_bytes(b"keep")
    redirected_build = fake_repo / "build"
    try:
        redirected_build.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    monkeypatch.setattr(build_module, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(build_module, "BUILD_DIR", redirected_build)
    monkeypatch.setattr(build_module, "OBFUSCATED_DIR", fake_repo / "obfuscated")
    monkeypatch.setattr(build_module, "DIST_DIR", fake_repo / "dist")
    build_module.clean()
    assert not redirected_build.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert venv_bytecode.exists()


def _write_generated_tree(root: Path) -> None:
    for relative in build.OBFUSCATED_MODULES:
        path = root / "obfuscated" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# generated {relative}\n", encoding="utf-8")


def test_obfuscation_accepts_expected_package_tree(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, "OBFUSCATED_DIR", tmp_path / "obfuscated")
    def generate(*args, **kwargs):
        _write_generated_tree(tmp_path)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
    monkeypatch.setattr(build.subprocess, "run", generate)
    assert build.obfuscate_source() is True
    assert all((tmp_path / "obfuscated" / relative).is_file() for relative in build.OBFUSCATED_MODULES)
    staged_root = Path(build._stage_obfuscated_source())
    assert build._validate_runtime_package(staged_root)
    assert all((staged_root / relative).read_text(encoding="utf-8").startswith("# generated")
               for relative in build.OBFUSCATED_MODULES)


def test_obfuscation_rejects_wrong_top_level_layout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, "OBFUSCATED_DIR", tmp_path / "obfuscated")
    def generate(*args, **kwargs):
        for relative in build.OBFUSCATED_MODULES:
            path = tmp_path / "obfuscated" / "core" / Path(relative).name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# wrong layout\n", encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
    monkeypatch.setattr(build.subprocess, "run", generate)
    assert build.obfuscate_source() is False
    assert not (tmp_path / "obfuscated").exists()


def test_failed_generation_removes_stale_output(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, "OBFUSCATED_DIR", tmp_path / "obfuscated")
    stale = tmp_path / "obfuscated" / "src" / "core"
    stale.mkdir(parents=True)
    (stale / "security.py").write_text("stale", encoding="utf-8")
    def fail(*args, **kwargs):
        return type("Result", (), {"returncode": 1, "stderr": "license error", "stdout": ""})()
    monkeypatch.setattr(build.subprocess, "run", fail)
    assert build.obfuscate_source() is False
    assert not (tmp_path / "obfuscated").exists()


def test_no_obfuscate_build_does_not_reuse_stale_output(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    stale = tmp_path / "obfuscated"
    stale.mkdir()
    (stale / "stale.marker").write_text("stale", encoding="utf-8")
    dist = tmp_path / "dist"
    monkeypatch.setattr(build, "OBFUSCATED_DIR", stale)
    monkeypatch.setattr(build, "DIST_DIR", dist)
    monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(build, "get_venv_executable", lambda name: name)
    calls = []
    def run(*args, **kwargs):
        calls.append(args[0])
        dist.mkdir(exist_ok=True)
        (dist / "SimLapsClient.exe").write_bytes(b"test")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
    monkeypatch.setattr(build.subprocess, "run", run)
    assert build.build_executable(use_obfuscated=False) is True
    assert calls and all("obfuscated" not in str(item) for item in calls[0])
    assert not stale.exists()
