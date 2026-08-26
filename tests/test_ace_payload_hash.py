"""Regression coverage for the standalone ACE payload helper."""

import re
from pathlib import Path

import httpx

from telemetry import ace_payload_hash

PAYLOAD = {
    "testID": "TEST-1",
    "replayDate": "2024-01-02",
    "replayTime": "12:34:56",
    "carID": "car.test",
    "carPreset": "preset.test",
    "trackID": "track.test",
    "layout": "main",
    "bestTimeMs": 123456,
    "steamId": "steam.test",
}


def test_payload_hash_remains_compatible() -> None:
    assert (
        ace_payload_hash.compute_payload_hash(PAYLOAD)
        == "d4ff4b50c85f67850f2cd9ca39bca9f4499eef3a"
    )


def test_helper_source_has_no_embedded_credential_or_personal_sample() -> None:
    source = Path(ace_payload_hash.__file__).read_text(encoding="utf-8")

    assert re.search(r"eyJ[a-zA-Z0-9_-]+\.", source) is None
    assert re.search(r"[\"']steamId[\"']\s*:\s*[\"']", source) is None
    assert "TOKEN =" not in source
    assert "verify=False" not in source
    assert "import requests" not in source


def test_missing_token_does_not_make_network_request(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("network must not be used without a token")

    monkeypatch.setattr(ace_payload_hash.httpx, "post", fail_if_called)

    assert ace_payload_hash.submit_payload(PAYLOAD, None) == {"error": "missing_token"}
    assert ace_payload_hash.submit_payload(PAYLOAD, "  ") == {"error": "missing_token"}


def test_submission_uses_bounded_timeout_and_verified_tls(monkeypatch) -> None:
    response = httpx.Response(200, json={"accepted": True})
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(ace_payload_hash.httpx, "post", fake_post)

    assert ace_payload_hash.submit_payload(PAYLOAD, "external-token") == {
        "accepted": True
    }
    assert captured["timeout"] == ace_payload_hash.REQUEST_TIMEOUT_SECONDS
    assert captured["timeout"] > 0
    assert captured["verify"] is True
    assert captured["headers"]["authorization"] == "Bearer external-token"


def test_submission_maps_transport_failures_to_stable_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        ace_payload_hash.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            httpx.TimeoutException("simulated timeout")
        ),
    )
    assert ace_payload_hash.submit_payload(PAYLOAD, "external-token") == {
        "error": "request_timeout"
    }

    monkeypatch.setattr(
        ace_payload_hash.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            httpx.ConnectError("simulated network error")
        ),
    )
    assert ace_payload_hash.submit_payload(PAYLOAD, "external-token") == {
        "error": "network_error"
    }
