"""
Debug Logs Viewer Component

Shows application debug logs in a popup window.
"""

import os
import sys
import threading
import time
from collections import deque
from datetime import datetime

import flet as ft

from .feedback import show_snackbar
from .mount_safe import safe_update


class SimpleLogCapture:
    """Simple, non-intrusive log capture system."""

    def __init__(self):
        self.logs = deque(maxlen=500)  # Keep last 500 log entries
        self.lock = threading.Lock()
        self.capture_enabled = True  # Start enabled by default

    def add_log(self, message, *, already_timestamped=False):
        """Add a log entry if capture is enabled."""
        if self.capture_enabled:
            with self.lock:
                if already_timestamped:
                    self.logs.append(message)
                else:
                    timestamp = time.strftime("%H:%M:%S")
                    self.logs.append(f"[{timestamp}] {message}")

    def get_logs(self) -> str:
        """Get captured logs as string."""
        with self.lock:
            if not self.logs:
                return "No logs captured yet."
            return "\n".join(self.logs)

    def clear_logs(self):
        """Clear all logs."""
        with self.lock:
            self.logs.clear()


# Global log capture instance
_log_capture = SimpleLogCapture()

_capture_lock = threading.RLock()
_capture_installed = False
_original_stdout = None
_original_stderr = None
_stdout_writer = None
_stderr_writer = None


class _UniversalWriter:
    """Mirror a stream while capturing ordinary writes in the debug viewer."""

    def __init__(self, original, capture):
        self.original = original
        self.capture = capture

    def write(self, text):
        if text is None:
            return 0
        if not isinstance(text, str):
            text = str(text)

        if self.original is not None:
            try:
                self.original.write(text)
            except (OSError, IOError, ValueError):
                # Expected when the underlying stream is closed during shutdown.
                pass

        if text.strip():
            for line in text.rstrip().splitlines():
                if line.strip():
                    self.capture.add_log(line.rstrip())

        return len(text)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except (OSError, IOError, ValueError):
                pass

    def isatty(self):
        if self.original is None:
            return False
        try:
            return self.original.isatty()
        except (OSError, IOError, AttributeError, ValueError):
            return False

    def __getattr__(self, name):
        """Preserve stream attributes used by libraries writing to stdout/stderr."""
        if self.original is None:
            raise AttributeError(name)
        return getattr(self.original, name)


class DebugLogsViewer:
    """A component to display debug logs in a popup window."""

    def __init__(self, page: ft.Page):
        self.page = page
        self.logs_text = ft.TextField(
            multiline=True,
            read_only=True,
            value="Loading logs...",
            height=400,
            width=600,
            bgcolor="#1a1a2e",
            text_style=ft.TextStyle(color="#ffffff", size=12, font_family="Consolas"),
        )
        self.clear_button = ft.OutlinedButton(
            "Clear Logs",
            on_click=self._clear_logs,
            style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
        )
        self.export_game_logs_button = ft.OutlinedButton(
            "Export Game Logs",
            on_click=self._export_game_logs,
            style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
        )
        self.close_button = ft.OutlinedButton(
            "Close",
            on_click=self._close_dialog,
            style=ft.ButtonStyle(color="#888888", side=ft.BorderSide(1, "#3d3d5c")),
        )

        self.dialog = None

    def _get_recent_logs(self) -> str:
        """Get recent log entries from capture."""
        logs = _log_capture.get_logs()
        status = "CAPTURE ACTIVE" if _log_capture.capture_enabled else "CAPTURE INACTIVE"

        # Separate telemetry logs for better visibility
        all_lines = logs.split('\n')
        telemetry_lines = [line for line in all_lines if '[TELEMETRY]' in line or '[ANALYZER]' in line]
        other_lines = [line for line in all_lines if '[TELEMETRY]' not in line and '[ANALYZER]' not in line]

        result = f"{status}\n\n"
        
        if telemetry_lines:
            result += "=== TELEMETRY EVENTS ===\n"
            result += "\n".join(telemetry_lines[-20:]) + "\n\n"  # Show last 20 telemetry events
            result += "=== OTHER LOGS ===\n"
        
        result += "\n".join(other_lines[-30:])  # Show last 30 other logs
        
        return result

    def _clear_logs(self, e=None):
        """Clear logs."""
        _log_capture.clear_logs()
        self.logs_text.value = "Logs cleared."
        safe_update(self.logs_text)

    def _export_game_logs(self, e=None):
        """Export game logs to file."""
        from ...utils.structured_logger import log_info, log_warning, log_error, log_exception, Component
        
        log_info(Component.DEBUG_LOGS, "Export game logs requested")
        
        try:
            # Get the app instance from the page
            app_instance = getattr(self.page, '_app_instance', None)
            
            if not app_instance:
                log_warning(Component.DEBUG_LOGS, "No app instance found")
                self._show_snackbar("App instance not available", "#ff6b6b")
                return
            
            if not hasattr(app_instance, '_log_parser'):
                log_warning(Component.DEBUG_LOGS, "App instance has no log parser")
                self._show_snackbar("Log parser not available", "#ff6b6b")
                return
            
            log_parser = app_instance._log_parser
            
            if not log_parser:
                log_warning(Component.DEBUG_LOGS, "Log parser is None")
                self._show_snackbar("Log parser not initialized", "#ff6b6b")
                return
            
            # Check if log buffer has content
            log_lines = log_parser.get_log_buffer()
            
            if not log_lines:
                log_warning(Component.DEBUG_LOGS, "Log buffer is empty")
                self._show_snackbar("No logs to export", "#ff6b6b")
                return
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
            filename = f"game_logs_{timestamp}.txt"
            filepath = os.path.join(downloads_path, filename)
            
            log_info(Component.DEBUG_LOGS, "Exporting game logs", filepath=filepath, lines=len(log_lines))
            
            # Export logs
            success = log_parser.export_logs_to_file(filepath)
            
            if success:
                log_info(Component.DEBUG_LOGS, "Game logs exported successfully", filename=filename)
                self._show_snackbar(f"Game logs exported to {filename}", "#51cf66")
                
                # Open the folder in Windows Explorer
                try:
                    import subprocess
                    subprocess.run(['explorer', '/select,', filepath], check=False)
                except Exception as ex:
                    log_warning(Component.DEBUG_LOGS, "Failed to open folder in Explorer", error=str(ex))
            else:
                log_error(Component.DEBUG_LOGS, "Failed to export game logs")
                self._show_snackbar("Failed to export game logs", "#ff6b6b")
                
        except Exception as ex:
            log_exception(Component.DEBUG_LOGS, "Error exporting game logs", ex)
            self._show_snackbar(f"Error: {str(ex)}", "#ff6b6b")
    
    def _show_snackbar(self, message: str, bgcolor: str):
        """Helper to show a snackbar message."""
        show_snackbar(self.page, message, bgcolor)

    def _close_dialog(self, e=None):
        """Close the debug logs dialog."""
        if self.dialog:
            self.dialog.open = False
            self.page.update()

    def show_dialog(self):
        """Show the debug logs dialog."""
        self.logs_text.value = self._get_recent_logs()

        self.dialog = ft.AlertDialog(
            title=ft.Text("Debug Logs", size=20, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column(
                    [
                        self.logs_text,
                        ft.Row([self.clear_button, self.export_game_logs_button, self.close_button], spacing=10),
                    ],
                    spacing=10,
                ),
                width=650,
                padding=20,
            ),
            shape=ft.RoundedRectangleBorder(radius=12),
        )

        self.page.show_dialog(self.dialog)


def start_log_capture():
    """Install the global log capture system once and return its active state."""
    global _capture_installed, _original_stdout, _original_stderr
    global _stdout_writer, _stderr_writer

    with _capture_lock:
        if _capture_installed:
            return False

        try:
            is_frozen = getattr(sys, "frozen", False)

            _original_stdout = getattr(sys, "stdout", None) or getattr(sys, "__stdout__", None)
            _original_stderr = getattr(sys, "stderr", None) or getattr(sys, "__stderr__", None)
            _stdout_writer = _UniversalWriter(_original_stdout, _log_capture)
            _stderr_writer = _UniversalWriter(_original_stderr, _log_capture)
            sys.stdout = _stdout_writer
            sys.stderr = _stderr_writer
            _capture_installed = True
            _log_capture.capture_enabled = True

            _log_capture.add_log("[LOGS] Universal debug capture enabled")
            _log_capture.add_log(f"[LOGS] Running as built executable: {is_frozen}")
            return True
        except Exception as e:
            # Restore whichever streams were replaced if installation failed partway.
            if sys.stdout is _stdout_writer:
                sys.stdout = _original_stdout
            if sys.stderr is _stderr_writer:
                sys.stderr = _original_stderr
            _stdout_writer = None
            _stderr_writer = None
            _original_stdout = None
            _original_stderr = None
            _capture_installed = False
            _log_capture.add_log(f"[LOGS] Failed to start log capture: {e}")
            try:
                import traceback

                _log_capture.add_log(traceback.format_exc())
            except (ImportError, OSError, IOError):
                pass
            return False


def stop_log_capture():
    """Restore streams installed by :func:`start_log_capture` exactly once."""
    global _capture_installed, _original_stdout, _original_stderr
    global _stdout_writer, _stderr_writer

    with _capture_lock:
        if not _capture_installed:
            return False

        # Do not overwrite a stream another owner installed after us.
        if sys.stdout is _stdout_writer:
            sys.stdout = _original_stdout
        if sys.stderr is _stderr_writer:
            sys.stderr = _original_stderr

        _capture_installed = False
        _log_capture.capture_enabled = False
        _stdout_writer = None
        _stderr_writer = None
        _original_stdout = None
        _original_stderr = None
        return True


def emit_structured_log(message: str, stream=None, *, capture=True):
    """Capture one preformatted structured event and optionally mirror it once.

    Structured logger messages already include a timestamp.  The event is sent
    directly to the viewer and console output bypasses our stream wrappers so a
    warning/error cannot be captured a second time.
    """
    if capture:
        _log_capture.add_log(message, already_timestamped=True)
    if stream is None:
        return

    target = getattr(stream, "original", stream)
    if target is None:
        return
    try:
        target.write(f"{message}\n")
        target.flush()
    except (OSError, IOError, ValueError, AttributeError):
        pass


def show_debug_logs(page: ft.Page):
    """Show debug logs dialog."""
    viewer = DebugLogsViewer(page)
    viewer.show_dialog()


# Global function to add logs from anywhere
def add_debug_log(message: str, *, already_timestamped=False):
    """Add a debug log entry."""
    _log_capture.add_log(message, already_timestamped=already_timestamped)
