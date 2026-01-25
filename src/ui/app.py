"""
Main Application UI - Coordinates all pages and core functionality.

Simplified: No authentication required. Uses signed payloads and
detects user from game logs automatically.
"""

import flet as ft
import asyncio
import os
import sys
from typing import Optional
from enum import Enum

from .pages.home import HomePage
from .pages.settings import SettingsPage
from .pages.history import HistoryPage, HistoryEntry
from .components.lap_card import LapCardStatus
from .components.status_bar import ConnectionStatus
from ..core.log_parser import LogParser, SessionData, LapData
from ..core.api_client import APIClient, SubmissionStatus
from ..core.security import get_steam_user
from ..utils.config import ConfigManager, AppConfig, get_config_manager


class AppPage(Enum):
    """Application pages."""
    HOME = "home"
    SETTINGS = "settings"
    HISTORY = "history"


class SimLapsApp:
    """
    Main application controller.
    
    No authentication required - uses signed payloads with embedded secret.
    User identity is detected from game logs (Steam ID).
    """
    
    def __init__(self, page: ft.Page):
        self.page = page
        self._setup_page()
        
        # Core services
        self._config_manager = get_config_manager()
        self._config = self._config_manager.load()
        self._api_client: Optional[APIClient] = None
        self._log_parser: Optional[LogParser] = None
        
        # Parser task
        self._parser_task: Optional[asyncio.Task] = None
        
        # Pages
        self._home_page: Optional[HomePage] = None
        self._settings_page: Optional[SettingsPage] = None
        self._history_page: Optional[HistoryPage] = None
        self._current_page = AppPage.HOME
        
        # History tracking
        self._history_entries: list[HistoryEntry] = []
        
        # Initialize
        self._init_services()
        self._init_pages()
        self._show_page(AppPage.HOME)
    
    def _setup_page(self):
        """Configure the Flet page."""
        self.page.title = "SimLaps Telemetry"
        self.page.width = 500
        self.page.height = 700
        self.page.bgcolor = "#0f0f1a"
        self.page.padding = 0
        self.page.spacing = 0
        
        # Set window icon
        icon_path = self._get_icon_path()
        if icon_path:
            self.page.window.icon = icon_path
        
        # Dark theme
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.theme = ft.Theme(
            color_scheme_seed="#7c3aed",
        )
        
        # Window close handler
        self.page.on_close = self._on_window_close
    
    def _get_icon_path(self) -> Optional[str]:
        """Get the path to the app icon (ICO for window icon)."""
        if getattr(sys, 'frozen', False):
            # Running as compiled executable - check _MEIPASS for bundled files
            if hasattr(sys, '_MEIPASS'):
                icon_path = os.path.join(sys._MEIPASS, "assets", "icon.ico")
                if os.path.exists(icon_path):
                    return icon_path
            # Fallback to executable directory
            base_path = os.path.dirname(sys.executable)
        else:
            # Running as script - go up from src/ui to project root
            base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # Try assets/icon.ico
        icon_path = os.path.join(base_path, "assets", "icon.ico")
        if os.path.exists(icon_path):
            return icon_path
        
        return None
    
    def _init_services(self):
        """Initialize core services."""
        # API client (no API key needed - uses signed payloads)
        self._api_client = APIClient(
            server_url=self._config.server_url,
        )
        
        # Log parser with callbacks
        self._log_parser = LogParser(
            log_path=self._config.log_path,
            on_lap_complete=self._on_lap_complete,
            on_status_change=self._on_parser_status,
            on_game_status_change=self._on_game_status_change,
            on_user_detected=self._on_user_detected,
            on_game_version=self._on_game_version,
        )
    
    def _init_pages(self):
        """Initialize page components."""
        self._home_page = HomePage(
            config=self._config,
            on_settings_click=lambda: self._show_page(AppPage.SETTINGS),
            on_history_click=lambda: self._show_page(AppPage.HISTORY),
        )
        
        self._settings_page = SettingsPage(
            config=self._config,
            on_back=lambda: self._show_page(AppPage.HOME),
            on_save=self._save_settings,
            on_test_connection=self._test_connection,
        )
        
        self._history_page = HistoryPage(
            on_back=lambda: self._show_page(AppPage.HOME),
            on_clear=self._clear_history,
        )
    
    def _show_page(self, page: AppPage):
        """Navigate to a page."""
        self._current_page = page
        
        # Clear existing controls
        self.page.clean()
        
        if page == AppPage.HOME:
            self.page.add(self._home_page)
        elif page == AppPage.SETTINGS:
            self.page.add(self._settings_page)
        elif page == AppPage.HISTORY:
            self._history_page.set_entries(self._history_entries)
            self.page.add(self._history_page)
    
    async def _on_lap_complete(self, session: SessionData, lap: LapData):
        """Handle completed lap from parser."""
        print(f"[APP] _on_lap_complete called: {lap.lap_time_str} on {session.track}")
        try:
            # Update detected user in UI
            if session.player_id:
                print(f"[APP] Updating detected user: {session.player_id}")
                self._home_page.set_detected_user(session.player_id, session.player_name)
            
            # Determine if we should submit this lap
            should_submit = self._config.auto_submit and (lap.is_valid or self._config.submit_invalid_laps)
            print(f"[APP] should_submit={should_submit}, is_valid={lap.is_valid}")
            
            # Determine initial status
            if not lap.is_valid and not self._config.submit_invalid_laps:
                status = LapCardStatus.INVALID
            else:
                status = LapCardStatus.SUBMITTING if should_submit else LapCardStatus.PENDING
            
            # Add to home page
            print(f"[APP] Adding lap card to home page...")
            card = self._home_page.add_lap(session, lap, status)
            print(f"[APP] Lap card added successfully")
            
            # Add to history
            history_entry = HistoryEntry(
                track=session.track,
                car=session.car,
                lap_time_ms=lap.lap_time_ms,
                timestamp=lap.timestamp,
                was_submitted=False,
                was_valid=lap.is_valid,
            )
            self._history_entries.append(history_entry)
            
            # Auto-submit if enabled
            if should_submit:
                print(f"[APP] Auto-submitting lap...")
                await self._submit_lap(card, session, lap, history_entry)
                print(f"[APP] Auto-submit complete")
        except Exception as e:
            print(f"[ERROR] _on_lap_complete failed: {e}")
            import traceback
            traceback.print_exc()
    
    async def _submit_lap(
        self,
        card,
        session: SessionData,
        lap: LapData,
        history_entry: HistoryEntry,
    ):
        """Submit a lap to the server."""
        card.update_status(LapCardStatus.SUBMITTING)
        
        try:
            result = await self._api_client.submit_lap(
                session=session,
                lap=lap,
                submit_invalid=self._config.submit_invalid_laps,
            )
        except Exception as e:
            card.update_status(LapCardStatus.FAILED, f"Submit error: {str(e)}")
            return
        
        if result is None:
            card.update_status(LapCardStatus.FAILED, "No response from server")
            return
        
        if result.status == SubmissionStatus.SUCCESS:
            card.update_status(LapCardStatus.SUBMITTED)
            history_entry.was_submitted = True
        elif result.status == SubmissionStatus.INVALID_LAP:
            card.update_status(LapCardStatus.INVALID, result.message)
        elif result.status == SubmissionStatus.GAME_NOT_RUNNING:
            card.update_status(LapCardStatus.FAILED, result.message)
        elif result.status == SubmissionStatus.SIGNATURE_ERROR:
            card.update_status(LapCardStatus.FAILED, result.message)
        elif result.status == SubmissionStatus.RATE_LIMITED:
            card.update_status(LapCardStatus.FAILED, result.message)
        elif result.status == SubmissionStatus.PLAUSIBILITY_FAILED:
            card.update_status(LapCardStatus.FAILED, result.message)
        else:
            card.update_status(LapCardStatus.FAILED, result.message)
    
    async def _on_parser_status(self, status: str):
        """Handle status update from parser."""
        if self._home_page:
            self._home_page.set_status(status)
    
    async def _on_game_status_change(self, is_running: bool):
        """Handle game running status change."""
        if self._home_page:
            self._home_page.set_game_running(is_running)
            
            if is_running:
                self._home_page.set_connection_status(
                    ConnectionStatus.CONNECTED,
                    "Session active - recording laps",
                )
            else:
                # Still connected/monitoring, just no active session
                self._home_page.set_connection_status(
                    ConnectionStatus.CONNECTED,
                    "Monitoring - waiting for session...",
                )
    
    async def _on_user_detected(self, steam_id: str, player_name: Optional[str]):
        """Handle user detection from log parser."""
        if self._home_page:
            self._home_page.set_detected_user(steam_id, player_name)
    
    async def _on_game_version(self, version: str):
        """Handle game version detection from log parser."""
        if self._home_page:
            self._home_page.set_game_version(version)
    
    def _on_window_close(self, e):
        """Handle window close."""
        self._cleanup()
    
    async def start_monitoring(self):
        """Start monitoring the log file."""
        if self._parser_task and not self._parser_task.done():
            return
        
        # Try to detect Steam user immediately from registry
        steam_id, steam_name = get_steam_user()
        if steam_id:
            self._home_page.set_detected_user(steam_id, steam_name)
        
        # Try to get game version from existing log file
        game_version = self._get_game_version_from_log()
        if game_version:
            self._home_page.set_game_version(game_version)
        
        # Set initial status - monitoring but not necessarily game running
        self._home_page.set_game_running(False)  # Will be set to True when session starts
        self._home_page.set_connection_status(
            ConnectionStatus.CONNECTED,
            "Monitoring log file...",
        )
        
        self._home_page.set_monitoring(True)
        # Use page.run_task() for proper Flet background task handling
        self._parser_task = self.page.run_task(self._run_parser)
    
    async def _run_parser(self):
        """Run the log parser in background."""
        try:
            await self._log_parser.follow()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._home_page.set_connection_status(
                ConnectionStatus.ERROR,
                f"Error: {str(e)}",
            )
        finally:
            self._home_page.set_monitoring(False)
    
    def stop_monitoring(self):
        """Stop monitoring the log file."""
        if self._log_parser:
            self._log_parser.stop()
        
        if self._parser_task:
            self._parser_task.cancel()
            self._parser_task = None
        
        self._home_page.set_monitoring(False)
        self._home_page.set_connection_status(
            ConnectionStatus.DISCONNECTED,
            "Monitoring stopped",
        )
    
    def _save_settings(self, config: AppConfig):
        """Save settings and apply changes."""
        self._config = config
        self._config_manager.save()
        
        # Update services with new settings
        self._api_client.set_server_url(config.server_url)
        
        # Restart parser if log path changed
        was_running = self._log_parser.is_running if self._log_parser else False
        
        if was_running:
            self.stop_monitoring()
        
        self._log_parser = LogParser(
            log_path=config.log_path,
            on_lap_complete=self._on_lap_complete,
            on_status_change=self._on_parser_status,
            on_game_status_change=self._on_game_status_change,
            on_user_detected=self._on_user_detected,
            on_game_version=self._on_game_version,
        )
        
        if was_running:
            self.page.run_task(self.start_monitoring)
        
        # Update home page
        self._home_page.update_config(self._config)
    
    async def _test_connection(self, server_url: str) -> tuple[bool, str]:
        """Test connection to server."""
        test_client = APIClient(server_url=server_url)
        return await test_client.test_connection()
    
    def _clear_history(self):
        """Clear lap history."""
        self._history_entries.clear()
    
    def _get_game_version_from_log(self) -> Optional[str]:
        """Read game version from the first few lines of the log file."""
        import re
        try:
            log_path = self._config.log_path
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    # Only read first 10 lines - version is at the top
                    for _ in range(10):
                        line = f.readline()
                        if not line:
                            break
                        if "Build release" in line:
                            match = re.search(r"Build release ([^,]+),", line)
                            if match:
                                return match.group(1)
        except Exception:
            pass
        return None
    
    def _cleanup(self):
        """Cleanup resources before exit."""
        self.stop_monitoring()
        
        if self._api_client:
            self.page.run_task(self._api_client.close)


async def main(page: ft.Page):
    """Application entry point for Flet."""
    app = SimLapsApp(page)
    
    # Start monitoring automatically
    await app.start_monitoring()


def run_app():
    """Run the Flet application."""
    ft.run(main)
