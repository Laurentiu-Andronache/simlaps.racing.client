"""Regression tests for secret-free release packaging."""

import sys
from types import SimpleNamespace

import pytest

import build
from src.core import security
from src.core.api_client import APIClient, SubmissionStatus


def test_artifact_plan_excludes_credentials() -> None:
    plan = build.build_artifact_plan()

    assert plan["forbidden_artifacts"] == (".env", "APP_SECRET", "SERVER_SECRET.txt")
    assert all(
        not any(secret_name in source for secret_name in plan["forbidden_artifacts"])
        for source, _destination in plan["data_files"]
    )


def test_build_command_does_not_pass_credentials_to_pyinstaller() -> None:
    command = build.build_command()
    command_text = " ".join(command)

    assert ".env" not in command_text
    assert "APP_SECRET" not in command_text
    assert "SERVER_SECRET.txt" not in command_text


def test_build_main_allows_offline_build_without_env(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["build.py", "--no-obfuscate"])
    monkeypatch.setattr(build, "check_dependencies", lambda: True)
    monkeypatch.setattr(build, "clean", lambda: None)
    monkeypatch.setattr(build, "build_executable", lambda: True)

    assert build.main() == 0


def test_clean_removes_stale_secret_outputs(tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / ".env").write_text("APP_SECRET=old-secret", encoding="utf-8")
    (dist / "SERVER_SECRET.txt").write_text("CLIENT_APP_SECRET=old-secret", encoding="utf-8")
    (dist / "SimLapsClient.exe").write_bytes(b"old build")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, "DIST_DIR", "dist")
    monkeypatch.setattr(build, "BUILD_DIR", "build")
    build.clean()

    assert not any(dist.iterdir())


def test_placeholder_secret_is_not_usable(monkeypatch) -> None:
    monkeypatch.setattr(security, "APP_SECRET", "blahtopsecret")

    assert security.is_secret_configured() is False
    with pytest.raises(RuntimeError, match="APP_SECRET"):
        security.get_app_secret()


@pytest.mark.asyncio
async def test_placeholder_secret_keeps_submission_offline(monkeypatch) -> None:
    monkeypatch.setattr(security, "APP_SECRET", "blahtopsecret")
    client = APIClient()
    session = SimpleNamespace(player_id="76561198321627695")
    lap = SimpleNamespace(
        lap_time_str="1:29.556",
        lap_time_ms=89556,
        is_valid=True,
        tyre_compound="SC",
    )

    result = await client.submit_lap(session, lap)

    assert result.status is SubmissionStatus.NO_SECRET
