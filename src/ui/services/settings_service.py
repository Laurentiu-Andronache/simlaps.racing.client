"""Settings apply/reconfigure service extracted from SimLapsApp.

Owns apply-settings orchestration: runtime service refresh, telemetry toggles,
and parser restart behavior.
"""

from typing import Any, Callable, TYPE_CHECKING

from src.core.analyzer import TelemetryAnalyzer
from src.core.discord_notifier import DiscordNotifier
from src.core.track_catalog import TRACK_CATALOG
from src.utils.structured_logger import Component, log_info
from src.utils.config import AppConfig
from ..components.telemetry_status import TelemetryButton

if TYPE_CHECKING:
    from ..app import SimLapsApp
    from src.core.discord_notifier import DiscordNotifier
    from src.core.pb_cache import PBCache
    from src.core.api_client import APIClient
    from src.core.log_parser import LogParser


class SettingsService:
    """Encapsulates settings persistence and runtime reconfiguration flow."""

    @staticmethod
    def _capture_snapshot(capture: Any) -> dict[str, Any] | None:
        """Take a restorable snapshot of capture configuration and buffers."""
        if capture is None:
            return None
        values: dict[str, Any] = {}
        state = vars(capture)
        for name in (
            "_record_frames",
            "_output_dir",
            "_debug_logs",
            "_frames",
            "_lap_boundaries",
            "_recording_awaiting_boundary",
            "_awaiting_lap_time_ms",
        ):
            if name in state:
                value = state[name]
                if isinstance(value, list):
                    value = list(value)
                values[name] = value
        if "_record_frames" not in values and "record_frames" in state:
            values["record_frames"] = state["record_frames"]
        return values

    @staticmethod
    def _restore_capture(capture: Any, snapshot: dict[str, Any] | None) -> None:
        """Restore capture state without invoking failure-prone mutators."""
        if capture is None or snapshot is None:
            return
        for name, value in snapshot.items():
            if isinstance(value, list):
                value = list(value)
            setattr(capture, name, value)

    @staticmethod
    def _ui_snapshot(home_page: Any) -> dict[str, Any] | None:
        """Snapshot HomePage fields touched by telemetry/settings updates."""
        if home_page is None:
            return None
        state = vars(home_page)
        snapshot: dict[str, Any] = {
            name: state[name]
            for name in ("config", "_telemetry_button", "_telemetry_button_container")
            if name in state
        }
        container = snapshot.get("_telemetry_button_container")
        if container is not None and "content" in vars(container):
            snapshot["_telemetry_button_container.content"] = container.content
        return snapshot

    @staticmethod
    def _restore_ui(home_page: Any, snapshot: dict[str, Any] | None) -> None:
        """Restore HomePage wiring directly, avoiding another failing update."""
        if home_page is None or snapshot is None:
            return
        for name, value in snapshot.items():
            if name != "_telemetry_button_container.content":
                setattr(home_page, name, value)
        container = snapshot.get("_telemetry_button_container")
        if container is not None and "_telemetry_button_container.content" in snapshot:
            container.content = snapshot["_telemetry_button_container.content"]

    @staticmethod
    def _restore_config_manager(manager: Any, config: AppConfig) -> None:
        """Best-effort persistent rollback, keeping memory coherent on errors."""
        try:
            if manager.set(config) is True:
                return
        except Exception:
            pass
        try:
            manager._config = config
            manager._loaded = True
            manager.save()
        except Exception:
            pass

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
        """Apply new config and reconcile dependent runtime services.

        Constructors are staged before persistence.  Persistence is the
        explicit commit boundary required by the existing settings contract;
        every operation after it is reversible and failures roll both runtime
        state and the persisted config back to the previous snapshot.
        """
        previous = app._config

        # Validate the enabled webhook before constructing or persisting any
        # replacement state. Disabled integrations may retain an old value,
        # but enabling Discord must never save a URL the notifier would reject.
        if config.discord_enabled and not DiscordNotifier.validate_webhook_url(
            config.discord_webhook_url
        ):
            raise ValueError("Invalid Discord webhook URL")

        old_discord = getattr(app, "_discord_notifier", None)
        old_pb_cache = app._pb_cache
        old_api_client = getattr(app, "_api_client", None)
        old_parser = getattr(app, "_log_parser", None)
        old_analyzer = getattr(app, "_telemetry_analyzer", None)
        old_button = getattr(app, "_telemetry_button", None)
        old_capture = getattr(app, "_telemetry_capture", None)
        old_session_lifecycle = getattr(app, "_session_lifecycle_service", None)
        old_session_capture = (
            getattr(old_session_lifecycle, "_telemetry_capture", None)
            if old_session_lifecycle is not None
            else None
        )
        old_home_page = getattr(app, "_home_page", None)
        old_button_path = getattr(old_button, "output_path", None)
        capture_snapshot = self._capture_snapshot(old_capture)
        home_snapshot = self._ui_snapshot(old_home_page)
        old_page_size = (getattr(app.page, "width", None), getattr(app.page, "height", None))

        server_changed = previous.server_url != config.server_url
        log_path_changed = previous.log_path != config.log_path
        telemetry_output_changed = previous.telemetry_output_path != config.telemetry_output_path
        telemetry_debug_changed = previous.telemetry_debug_logs != config.telemetry_debug_logs

        current_discord = old_discord
        discord_changed = (
            previous.discord_enabled != config.discord_enabled
            or previous.discord_webhook_url != config.discord_webhook_url
        )
        if config.discord_webhook_url and config.discord_enabled:
            staged_discord = (
                create_discord_notifier(config.discord_webhook_url)
                if discord_changed or current_discord is None
                else current_discord
            )
        else:
            staged_discord = None

        staged_pb_cache = old_pb_cache
        if server_changed:
            staged_pb_cache = get_pb_cache_for_server(config.server_url)

        staged_api_client = old_api_client
        if old_api_client is None or server_changed:
            staged_api_client = create_api_client(
                server_url=config.server_url,
                session_manager=app._session_manager,
            )

        staged_parser = old_parser
        if old_parser is None or log_path_changed:
            staged_parser = create_log_parser(config.log_path)

        staged_analyzer = old_analyzer
        staged_button = old_button
        if config.telemetry_enabled:
            if staged_analyzer is None or telemetry_output_changed:
                staged_analyzer = TelemetryAnalyzer(
                    output_dir=config.telemetry_output_path,
                    track_catalog=TRACK_CATALOG,
                    session_manager=app._session_manager,
                )
            if staged_button is None:
                staged_button = TelemetryButton(
                    on_click=getattr(app, "_open_telemetry_location", None),
                    output_path=config.telemetry_output_path,
                )

        was_running = bool(old_parser and old_parser.is_running)
        monitoring_stopped = False
        parser_restart_scheduled = False

        # Persist only after all imports and constructors succeeded.
        if app._config_manager.set(config) is False:
            raise OSError("Could not save application settings")

        try:
            if log_path_changed and was_running:
                # Mark this before calling into MonitoringService because its
                # parser.stop/UI callbacks may themselves raise halfway
                # through.  Rollback must still attempt to re-arm the old
                # parser in that case.
                monitoring_stopped = True
                app.stop_monitoring()

            app._config = config
            app._discord_notifier = staged_discord
            app._pb_cache = staged_pb_cache
            app._api_client = staged_api_client
            app._log_parser = staged_parser

            # Reconcile telemetry capture mode with the new setting.  The
            # capture loop remains alive for validity-only SHM updates.
            if config.telemetry_enabled and not app._telemetry_capture:
                log_info(Component.APP, "Telemetry enabled - initializing services")
                app._init_telemetry_services()
                if app._telemetry_capture is None:
                    raise RuntimeError("Telemetry capture could not be initialized")
                if app._telemetry_analyzer is None:
                    app._telemetry_analyzer = staged_analyzer
                if app._telemetry_button is None:
                    app._telemetry_button = staged_button
                app._attach_telemetry_ui()
                if app._telemetry_capture:
                    app.page.run_task(app._start_telemetry_capture)
            elif config.telemetry_enabled and app._telemetry_capture:
                if not app._telemetry_capture.record_frames:
                    app._telemetry_capture.set_record_frames(True)
                    log_info(Component.APP, "Telemetry recording enabled")
                if telemetry_output_changed or telemetry_debug_changed:
                    app._telemetry_capture.configure(
                        output_dir=config.telemetry_output_path,
                        debug_logs=config.telemetry_debug_logs,
                    )
                app._telemetry_analyzer = staged_analyzer
                app._telemetry_button = staged_button
                assert app._telemetry_button is not None
                app._telemetry_button.update_path(config.telemetry_output_path)
                app._attach_telemetry_ui()
            elif not config.telemetry_enabled and app._telemetry_capture:
                if app._telemetry_capture.record_frames:
                    app._telemetry_capture.set_record_frames(False)
                    log_info(Component.APP, "Telemetry recording disabled - validity-only mode active")
                if telemetry_output_changed or telemetry_debug_changed:
                    app._telemetry_capture.configure(
                        output_dir=config.telemetry_output_path,
                        debug_logs=config.telemetry_debug_logs,
                    )
                app._telemetry_analyzer = None
                if app._home_page:
                    app._home_page.set_telemetry_button(None, "")
                app._telemetry_button = None

            if app._home_page:
                app._home_page.update_config(app._config)

            if previous.window_width != config.window_width:
                app.page.width = config.window_width
            if previous.window_height != config.window_height:
                app.page.height = config.window_height

            if log_path_changed and was_running:
                app.page.run_task(app.start_monitoring)
                parser_restart_scheduled = True

            # Close the old client only after all new state is coherent.
            if old_api_client is not None and old_api_client is not staged_api_client:
                app.page.run_task(old_api_client.close)
        except Exception:
            if parser_restart_scheduled:
                try:
                    app.stop_monitoring()
                except Exception:
                    pass

            app._config = previous
            app._discord_notifier = old_discord
            app._pb_cache = old_pb_cache
            app._api_client = old_api_client
            app._log_parser = old_parser
            app._telemetry_capture = old_capture
            app._telemetry_analyzer = old_analyzer
            app._telemetry_button = old_button
            if old_session_lifecycle is not None:
                old_session_lifecycle._telemetry_capture = old_session_capture
            self._restore_capture(old_capture, capture_snapshot)
            self._restore_ui(old_home_page, home_snapshot)
            if old_button is not None:
                try:
                    old_button.output_path = old_button_path
                except Exception:
                    pass
            app.page.width, app.page.height = old_page_size

            if monitoring_stopped and was_running:
                try:
                    app.page.run_task(app.start_monitoring)
                except Exception:
                    pass

            # A staged client has not been published yet.  Normally its
            # underlying HTTP client is still None, but close it when a
            # factory supplied an already-open implementation so rollback
            # cannot strand a resource.
            if staged_api_client is not old_api_client and staged_api_client is not None:
                staged_client = vars(staged_api_client).get("_client")
                if staged_client is not None:
                    try:
                        app.page.run_task(staged_api_client.close)
                    except Exception:
                        pass

            self._restore_config_manager(app._config_manager, previous)
            raise
