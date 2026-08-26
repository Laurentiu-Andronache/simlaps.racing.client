"""Additional coverage tests for src.core.pb_cache."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.pb_cache import PBCache, PersonalBest


@pytest.mark.asyncio
async def test_preload_from_api_handles_non_200_response() -> None:
    cache = PBCache("https://simlaps.racing")

    with patch("httpx.AsyncClient") as mock_client:
        response = MagicMock()
        response.status_code = 503
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)

        ok = await cache.preload_from_api("76561198000000000")

    assert ok is False
    assert cache.is_loaded() is False


@pytest.mark.asyncio
async def test_preload_from_api_handles_timeout_and_request_error() -> None:
    cache = PBCache("https://simlaps.racing")

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        assert await cache.preload_from_api("steam") is False

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.RequestError("request")
        )
        assert await cache.preload_from_api("steam") is False


@pytest.mark.asyncio
async def test_preload_from_api_rejects_malformed_rows_without_partial_state() -> None:
    cache = PBCache("https://simlaps.racing")

    valid_payload = {
        "personalBests": [
            {
                "trackId": "spa",
                "carId": "car",
                "bestTime": 120000,
                "setAt": "2026-01-01T00:00:00Z",
            },
        ]
    }
    malformed_payload = {
        "personalBests": [
            {"trackId": "monza", "carId": "car", "bestTime": 115000},
            {"trackId": "", "carId": "car", "bestTime": 120000},
        ]
    }

    with patch("httpx.AsyncClient") as mock_client:
        first_response = MagicMock(status_code=200)
        first_response.json.return_value = valid_payload
        second_response = MagicMock(status_code=200)
        second_response.json.return_value = malformed_payload
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=[first_response, second_response]
        )

        assert await cache.preload_from_api("user-a") is True
        state_before_failed_preload = cache.get_all_pbs()
        assert await cache.preload_from_api("user-b") is False

    assert cache.get_all_pbs() == state_before_failed_preload
    assert cache.is_loaded() is True
    assert cache.get_steam_id() == "user-a"
    assert ("spa", "car") in cache.get_all_pbs()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"personalBests": {}},
        {"personalBests": None},
        {"personalBests": [None]},
        {"personalBests": [{"trackId": "spa", "carId": "car"}]},
        {
            "personalBests": [
                {
                    "trackId": "spa",
                    "carId": "car",
                    "bestTime": 120000,
                    "setAt": "not-a-date",
                }
            ]
        },
    ],
)
async def test_preload_from_api_preserves_state_for_malformed_response(payload) -> None:
    cache = PBCache("https://simlaps.racing")
    valid_payload = {
        "personalBests": [{"trackId": "spa", "carId": "car", "bestTime": 120000}]
    }

    with patch("httpx.AsyncClient") as mock_client:
        valid_response = MagicMock(status_code=200)
        valid_response.json.return_value = valid_payload
        malformed_response = MagicMock(status_code=200)
        malformed_response.json.return_value = payload
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=[valid_response, malformed_response]
        )

        assert await cache.preload_from_api("user-a") is True
        state_before_failed_preload = cache.get_all_pbs()
        assert await cache.preload_from_api("user-b") is False

    assert cache.get_all_pbs() == state_before_failed_preload
    assert cache.get_steam_id() == "user-a"
    assert cache.is_loaded() is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "best_time",
    [0, -1, 120000.0, "120000", None, True, False, float("nan"), float("inf")],
)
async def test_preload_from_api_rejects_non_positive_or_non_integer_times(
    best_time,
) -> None:
    cache = PBCache("https://simlaps.racing")
    valid_payload = {
        "personalBests": [{"trackId": "spa", "carId": "car", "bestTime": 120000}]
    }
    malformed_payload = {
        "personalBests": [
            {"trackId": "spa", "carId": "car", "bestTime": best_time}
        ]
    }

    with patch("httpx.AsyncClient") as mock_client:
        valid_response = MagicMock(status_code=200)
        valid_response.json.return_value = valid_payload
        malformed_response = MagicMock(status_code=200)
        malformed_response.json.return_value = malformed_payload
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=[valid_response, malformed_response]
        )

        assert await cache.preload_from_api("user-a") is True
        state_before_failed_preload = cache.get_all_pbs()
        assert await cache.preload_from_api("user-b") is False

    assert cache.get_all_pbs() == state_before_failed_preload
    assert cache.get_steam_id() == "user-a"
    assert cache.is_loaded() is True
    all_pbs = cache.get_all_pbs()
    assert len(all_pbs) == 1
    assert ("spa", "car") in all_pbs


@pytest.mark.asyncio
async def test_timestamps_are_normalized_to_utc_for_stats() -> None:
    cache = PBCache("https://simlaps.racing")
    payload = {
        "personalBests": [
            {
                "trackId": "spa",
                "carId": "car",
                "bestTime": 120000,
                "setAt": "2024-01-01T12:00:00+02:00",
            },
            {
                "trackId": "imola",
                "carId": "car",
                "bestTime": 121000,
                "setAt": "2024-01-01T11:00:00Z",
            },
            {
                "trackId": "mugello",
                "carId": "car",
                "bestTime": 122000,
                "setAt": "2024-01-01T12:00:00",
            },
        ]
    }

    with patch("httpx.AsyncClient") as mock_client:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = payload
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)

        assert await cache.preload_from_api("steam") is True

    api_pb = cache.get_personal_best("spa", "car")
    assert api_pb is not None
    assert api_pb.updated_at == datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    assert cache.get_personal_best("imola", "car").updated_at == datetime(
        2024, 1, 1, 11, tzinfo=timezone.utc
    )
    assert cache.get_personal_best("mugello", "car").updated_at == datetime(
        2024, 1, 1, 12, tzinfo=timezone.utc
    )

    assert cache.check_and_update_pb("monza", "car", 110000) is True
    local_pb = cache.get_personal_best("monza", "car")
    assert local_pb is not None
    assert local_pb.updated_at is not None
    assert local_pb.updated_at.tzinfo is timezone.utc

    stats = cache.get_cache_stats()
    assert stats["oldest_entry"] == datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    assert stats["newest_entry"] == local_pb.updated_at


def test_personal_best_normalizes_naive_and_offset_timestamps() -> None:
    naive = PersonalBest(best_time_ms=1000, updated_at=datetime(2024, 1, 1, 12))
    offset = PersonalBest(
        best_time_ms=1000,
        updated_at=datetime.fromisoformat("2024-01-01T12:00:00+02:00"),
    )

    assert naive.updated_at == datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
    assert offset.updated_at == datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
