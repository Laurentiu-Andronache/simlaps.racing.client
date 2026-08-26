"""Regression tests for the isolated PyArmor build workflow."""

from pathlib import Path

import build


def _write_generated_tree(root: Path) -> None:
    for relative in build.OBFUSCATED_MODULES:
        path = root / "obfuscated" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# generated {relative}\n", encoding="utf-8")


def test_obfuscation_accepts_expected_package_tree(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def generate(*args, **kwargs):
        _write_generated_tree(tmp_path)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(build.subprocess, "run", generate)

    assert build.obfuscate_source() is True
    assert all(
        (tmp_path / "obfuscated" / relative).is_file()
        for relative in build.OBFUSCATED_MODULES
    )

    # Staging must keep the generated replacements after copying the rest of
    # the source package, and each fully-qualified module must resolve there.
    monkeypatch.setattr(build, "OBFUSCATED_DIR", str(tmp_path / "obfuscated"))
    staged_root = Path(build._stage_obfuscated_source())
    assert build._validate_runtime_package(staged_root)
    assert all(
        (staged_root / relative).read_text(encoding="utf-8").startswith("# generated")
        for relative in build.OBFUSCATED_MODULES
    )


def test_obfuscation_rejects_wrong_top_level_layout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

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

    # The explicit mode argument is the guard: merely having a directory on
    # disk must not opt this build into obfuscated package resolution.
    build_module = build
    monkeypatch.setattr(build_module, "get_venv_executable", lambda name: name)

    calls = []

    def run(*args, **kwargs):
        calls.append(args[0])
        dist.mkdir(exist_ok=True)
        (dist / "SimLapsClient.exe").write_bytes(b"test")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(build_module.subprocess, "run", run)
    assert build_module.build_executable(use_obfuscated=False) is True
    assert calls
    assert all("obfuscated" not in str(item) for item in calls[0])
    assert not stale.exists()
