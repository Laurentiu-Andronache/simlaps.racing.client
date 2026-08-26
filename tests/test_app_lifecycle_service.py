from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from src.ui.app import SimLapsApp
from src.ui.services.app_lifecycle_service import AppLifecycleService


def _make_app(order=None) -> SimpleNamespace:
    order = order if order is not None else []
    app = SimpleNamespace()
    app.page = SimpleNamespace(
        window=SimpleNamespace(
            destroy=AsyncMock(side_effect=lambda: order.append("destroy")),
        )
    )
    app._stop_telemetry_capture = AsyncMock(
        side_effect=lambda **kwargs: order.append("telemetry")
    )
    app._api_client = SimpleNamespace(
        close=AsyncMock(side_effect=lambda: order.append("api")),
    )
    app.stop_monitoring = MagicMock(side_effect=lambda: order.append("monitor"))
    return app


@pytest.mark.asyncio
async def test_cleanup_stops_monitor_finalizes_telemetry_closes_api_before_destroy():
    order = []
    app = _make_app(order)

    await AppLifecycleService().cleanup(app=app)

    assert order == ["monitor", "telemetry", "api", "destroy"]
    app._stop_telemetry_capture.assert_awaited_once_with(reason="app_close")
    app._api_client.close.assert_awaited_once_with()
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_for_repeated_events():
    app = _make_app()
    service = AppLifecycleService()

    await service.cleanup(app=app)
    await service.cleanup(app=app)

    app.stop_monitoring.assert_called_once_with()
    app._stop_telemetry_capture.assert_awaited_once_with(reason="app_close")
    app._api_client.close.assert_awaited_once_with()
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_logs_failure_and_continues_remaining_steps():
    order = []
    app = _make_app(order)
    app.stop_monitoring.side_effect = RuntimeError("monitor failed")
    app._stop_telemetry_capture.side_effect = RuntimeError("telemetry failed")
    app._api_client.close.side_effect = RuntimeError("api failed")

    with patch("src.ui.services.app_lifecycle_service.log_exception") as log_error:
        await AppLifecycleService().cleanup(app=app)

    assert order == ["destroy"]
    assert log_error.call_count == 3
    assert "monitor shutdown" in log_error.call_args_list[0].args[1]
    assert "telemetry shutdown" in log_error.call_args_list[1].args[1]
    assert "API client close" in log_error.call_args_list[2].args[1]
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_native_close_event_uses_async_cleanup():
    app = SimLapsApp.__new__(SimLapsApp)
    app._app_lifecycle_service = MagicMock()
    app._app_lifecycle_service.cleanup = AsyncMock()

    await app._on_window_event(
        ft.WindowEvent(
            name="window",
            control=MagicMock(),
            type=ft.WindowEventType.CLOSE,
        )
    )
    await app._on_window_event(
        ft.WindowEvent(
            name="window",
            control=MagicMock(),
            type=ft.WindowEventType.FOCUS,
        )
    )

    app._app_lifecycle_service.cleanup.assert_awaited_once_with(app=app)
