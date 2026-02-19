"""
Debug Logs Viewer Component

Shows application debug logs in a popup window.
"""

import sys
import threading
import time
from collections import deque

import flet as ft


class SimpleLogCapture:
    """Simple, non-intrusive log capture system."""

    def __init__(self):
        self.logs = deque(maxlen=500)  # Keep last 500 log entries
        self.lock = threading.Lock()
        self.capture_enabled = True  # Start enabled by default

    def enable_capture(self):
        """Enable log capture only when needed."""
        self.capture_enabled = True

    def disable_capture(self):
        """Disable log capture."""
        self.capture_enabled = False

    def add_log(self, message):
        """Add a log entry if capture is enabled."""
        if self.capture_enabled:
            timestamp = time.strftime("%H:%M:%S")
            with self.lock:
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
        self.clear_button = ft.ElevatedButton(
            "Clear Logs",
            icon=ft.Icons.CLEAR,
            on_click=self._clear_logs,
            style=ft.ButtonStyle(bgcolor="#7c3aed"),
        )
        self.close_button = ft.ElevatedButton(
            "Close",
            icon=ft.Icons.CLOSE,
            on_click=self._close_dialog,
            style=ft.ButtonStyle(bgcolor="#6b7280"),
        )

        self.dialog = None

    def _get_recent_logs(self) -> str:
        """Get recent log entries from capture."""
        logs = _log_capture.get_logs()
        status = "CAPTURE ACTIVE" if _log_capture.capture_enabled else "CAPTURE INACTIVE"

        return f"{status}\n\n{logs}"

    def _clear_logs(self, e=None):
        """Clear the logs."""
        _log_capture.clear_logs()
        self.logs_text.value = "Logs cleared."
        self.logs_text.update()

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
                        ft.Row([self.clear_button, self.close_button], spacing=10),
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
    """Start the global log capture system (smart, non-intrusive)."""
    try:
        is_frozen = getattr(sys, "frozen", False)

        original_stdout = getattr(sys, "stdout", None) or getattr(sys, "__stdout__", None)
        original_stderr = getattr(sys, "stderr", None) or getattr(sys, "__stderr__", None)

        class UniversalWriter:
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
                    except Exception:
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
                    except Exception:
                        pass

            def isatty(self):
                if self.original is None:
                    return False
                try:
                    return self.original.isatty()
                except Exception:
                    return False

        sys.stdout = UniversalWriter(original_stdout, _log_capture)
        sys.stderr = UniversalWriter(original_stderr, _log_capture)

        _log_capture.add_log("[LOGS] Universal debug capture enabled")
        _log_capture.add_log(f"[LOGS] Running as built executable: {is_frozen}")

    except Exception as e:
        _log_capture.add_log(f"[LOGS] Failed to start log capture: {e}")
        try:
            import traceback

            _log_capture.add_log(traceback.format_exc())
        except Exception:
            pass



def show_debug_logs(page: ft.Page):
    """Show debug logs dialog."""
    viewer = DebugLogsViewer(page)
    viewer.show_dialog()


# Global function to add logs from anywhere
def add_debug_log(message: str):
    """Add a debug log entry."""
    _log_capture.add_log(message)
