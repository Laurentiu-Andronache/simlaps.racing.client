"""Settings apply/reconfigure service extracted from SimLapsApp.

Owns apply-settings orchestration: runtime service refresh, telemetry toggles,
and parser restart behavior.
"""

from typing import Callable, TYPE_CHECKING

from src.utils.structured_logger import Component, log_info
from src.utils.config import AppConfig

if TYPE_CHECKING:
    from ..app import SimLapsApp
    from src.core.discord_notifier import DiscordNotifier
    from src.core.pb_cache import PBCache
    from src.core.api_client import APIClient
    from src.core.log_parser import LogParser


class SettingsService:
    """Encapsulates settings persistence and runtime reconfiguration flow."""

    def apply(
        self,
        *,
        app: "SimLapsApp",
        config: AppConfig,
        create_discord_notifier: "Callable[[str], DiscordNotifier]",
        get_pb_cache_for_server: "Callable[[str], PBCache]",
        create_api_client: "Callable[..., APIClient]",
        create_log_parser: "Callable[[str], LogParser]",
    ) -> None:
        """Apply new config and reconcile dependent runtime services."""
        app._config = config
        app._config_manager.save()

        # Update Discord notifier
        if config.discord_webhook_url and config.discord_enabled:
            app._discord_notifier = create_discord_notifier(config.discord_webhook_url)
        else:
            app._discord_notifier = None

        # Update PB cache if server URL changed
        if app._pb_cache.server_url != config.server_url:
            app._pb_cache = get_pb_cache_for_server(config.server_url)

        # Update API client
        app._api_client = create_api_client(
            server_url=config.server_url,
            session_manager=app._session_manager,
        )

        # Update services with new settings
        app._api_client.set_server_url(config.server_url)

        # Reconcile telemetry capture mode with the new setting.
        # The capture loop always runs (needed for SHM lap validity), but
        # frame recording and analysis are gated on telemetry_enabled.
        if config.telemetry_enabled and not app._telemetry_capture:
            # First time enabling — full init (should not happen now that
            # _init_telemetry_services always creates the capture, but
            # keep as a safety net).
            log_info(Component.APP, "Telemetry enabled - initializing services")
            app._init_telemetry_services()
            app._attach_telemetry_ui()
            if app._telemetry_capture:
                app.page.run_task(app._start_telemetry_capture)
        elif config.telemetry_enabled and app._telemetry_capture:
            # Telemetry was already enabled or was in validity-only mode;
            # switch to full recording and ensure analyzer/button exist.
            if not app._telemetry_capture.record_frames:
                app._telemetry_capture.set_record_frames(True)
                log_info(Component.APP, "Telemetry recording enabled")
            if not app._telemetry_analyzer:
                from src.core.analyzer import TelemetryAnalyzer
                from src.core.track_catalog import TRACK_CATALOG
                app._telemetry_analyzer = TelemetryAnalyzer(
                    output_dir=config.telemetry_output_path,
                    track_catalog=TRACK_CATALOG,
                    session_manager=app._session_manager,
                )
            if not app._telemetry_button:
                from .components.telemetry_status import TelemetryButton
                app._telemetry_button = TelemetryButton(
                    on_click=app._open_telemetry_location,
                    output_path=config.telemetry_output_path,
                )
                app._attach_telemetry_ui()
        elif not config.telemetry_enabled and app._telemetry_capture:
            # User disabled telemetry recording — switch to validity-only
            # mode.  The capture loop stays alive so SHM validity data
            # continues to flow to the shared session.
            if app._telemetry_capture.record_frames:
                app._telemetry_capture.set_record_frames(False)
                log_info(Component.APP, "Telemetry recording disabled — validity-only mode active")
            # Drop analyzer and UI button (no frames to analyze).
            app._telemetry_analyzer = None
            if app._home_page:
                app._home_page.set_telemetry_button(None, "")
            app._telemetry_button = None

        # Restart parser if log path changed
        was_running = app._log_parser.is_running if app._log_parser else False

        if was_running:
            app.stop_monitoring()

        app._log_parser = create_log_parser(config.log_path)

        if was_running:
            app.page.run_task(app.start_monitoring)

        # Update home page
        app._home_page.update_config(app._config)
