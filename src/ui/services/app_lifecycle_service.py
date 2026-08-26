"""App lifecycle/shutdown service extracted from SimLapsApp.

Owns app close-path orchestration and keeps all asynchronous cleanup in one
ordered, idempotent path.
"""

import inspect
from typing import TYPE_CHECKING

from src.utils.structured_logger import Component, log_debug, log_exception

if TYPE_CHECKING:
    from ..app import SimLapsApp


class AppLifecycleService:
    """Encapsulates idempotent app cleanup sequencing."""

    def __init__(self) -> None:
        self._cleanup_started = False

    async def cleanup(self, *, app: "SimLapsApp") -> None:
        """Run app shutdown sequence once, awaiting each step in order.

        Native window close and page disconnect notifications can arrive close
        together.  Marking cleanup as started before the first await makes all
        later notifications no-ops while allowing the original caller to wait
        for the complete shutdown sequence.
        """
        if self._cleanup_started:
            log_debug(Component.APP, "Cleanup already executed; skipping duplicate call")
            return

        self._cleanup_started = True

        # Keep this order deliberate: stop producers first, then finalize any
        # recorded telemetry (including analysis/report generation), close the
        # HTTP client, and only then let the native window be destroyed.
        await self._run_step(
            "monitor shutdown",
            app.stop_monitoring,
        )
        telemetry_stop = getattr(app, "_stop_telemetry_capture", None)
        if telemetry_stop:
            await self._run_step(
                "telemetry shutdown",
                telemetry_stop,
                reason="app_close",
            )

        api_client = getattr(app, "_api_client", None)
        if api_client:
            await self._run_step("API client close", api_client.close)

        page = getattr(app, "page", None)
        window = getattr(page, "window", None) if page else None
        if window:
            await self._run_step("window destroy", window.destroy)

    @staticmethod
    async def _run_step(label: str, callback, **kwargs) -> None:
        """Run one cleanup step and continue if it fails."""
        try:
            result = callback(**kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            log_exception(Component.APP, f"Cleanup failed during {label}", exc)
