"""
Settings Page - Configuration options.

Simplified: No API key required (uses signed payloads).
"""

import flet as ft
from typing import Optional, Callable
from pathlib import Path

from ...utils.config import AppConfig, DEFAULT_LOG_PATH, DEFAULT_SERVER_URL
from ...core.security import get_security_status


class SettingsPage(ft.Container):
    """
    Settings page for configuring the application.
    
    Note: No API key field - authentication uses signed payloads with
    an embedded app secret.
    """
    
    def __init__(
        self,
        config: AppConfig,
        on_back: Optional[Callable] = None,
        on_save: Optional[Callable[[AppConfig], None]] = None,
        on_test_connection: Optional[Callable] = None,
    ):
        self.config = config
        self.on_back = on_back
        self.on_save = on_save
        self.on_test_connection = on_test_connection
        
        # Form fields
        self._log_path_field = ft.TextField(
            value=config.log_path,
            label="ACE Log File Path",
            hint_text=DEFAULT_LOG_PATH,
            border_color="#3d3d5c",
            focused_border_color="#7c3aed",
            bgcolor="#1e1e2e",
            color="#ffffff",
            label_style=ft.TextStyle(color="#888888"),
            suffix=ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN,
                icon_color="#888888",
                tooltip="Browse...",
                on_click=self._browse_log_file,
            ),
        )
        
        self._server_url_field = ft.TextField(
            value=config.server_url,
            label="Server URL",
            hint_text=DEFAULT_SERVER_URL,
            border_color="#3d3d5c",
            focused_border_color="#7c3aed",
            bgcolor="#1e1e2e",
            color="#ffffff",
            label_style=ft.TextStyle(color="#888888"),
        )
        
        self._auto_submit_switch = ft.Switch(
            value=config.auto_submit,
            active_color="#7c3aed",
        )
        
        self._submit_invalid_switch = ft.Switch(
            value=config.submit_invalid_laps,
            active_color="#7c3aed",
        )
        
        self._minimize_to_tray_switch = ft.Switch(
            value=config.minimize_to_tray,
            active_color="#7c3aed",
        )
        
        self._start_minimized_switch = ft.Switch(
            value=config.start_minimized,
            active_color="#7c3aed",
        )
        
        self._connection_status = ft.Text(
            "",
            size=12,
            color="#888888",
        )
        
        super().__init__(
            content=self._build_content(),
            expand=True,
        )
    
    def _build_content(self) -> ft.Control:
        """Build the settings page content."""
        # Header with back button
        header = ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    icon_color="#ffffff",
                    on_click=lambda _: self.on_back() if self.on_back else None,
                ),
                ft.Text(
                    "Settings",
                    size=24,
                    weight=ft.FontWeight.W_700,
                    color="#ffffff",
                ),
            ],
            spacing=8,
        )
        
        # Path settings section
        path_section = self._build_section(
            "File Paths",
            [
                self._log_path_field,
            ],
        )
        
        # Server settings section
        server_section = self._build_section(
            "Server",
            [
                self._server_url_field,
                ft.Row(
                    controls=[
                        ft.OutlinedButton(
                            "Test Connection",
                            icon=ft.Icons.WIFI,
                            on_click=self._test_connection,
                            style=ft.ButtonStyle(
                                color="#888888",
                                side=ft.BorderSide(1, "#3d3d5c"),
                            ),
                        ),
                        self._connection_status,
                    ],
                    spacing=16,
                ),
            ],
        )
        
        # Behavior settings section
        behavior_section = self._build_section(
            "Behavior",
            [
                self._build_switch_row(
                    "Auto-submit valid laps",
                    "Automatically submit laps when completed",
                    self._auto_submit_switch,
                ),
                ft.Divider(color="#2d2d4a", height=1),
                self._build_switch_row(
                    "Submit invalid laps",
                    "Also submit laps with penalties or off-track",
                    self._submit_invalid_switch,
                ),
                ft.Divider(color="#2d2d4a", height=1),
                self._build_switch_row(
                    "Minimize to tray",
                    "Keep running in system tray when closed",
                    self._minimize_to_tray_switch,
                ),
                ft.Divider(color="#2d2d4a", height=1),
                self._build_switch_row(
                    "Start minimized",
                    "Start in system tray on launch",
                    self._start_minimized_switch,
                ),
            ],
        )
        
        # Security info section
        security_section = self._build_security_section()
        
        # Save button
        save_button = ft.ElevatedButton(
            "Save Settings",
            icon=ft.Icons.SAVE,
            on_click=self._save_settings,
            style=ft.ButtonStyle(
                bgcolor="#7c3aed",
                color="#ffffff",
                padding=16,
            ),
            width=200,
        )
        
        # Reset button
        reset_button = ft.TextButton(
            "Reset to Defaults",
            on_click=self._reset_settings,
            style=ft.ButtonStyle(color="#888888"),
        )
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Container(height=16),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                path_section,
                                server_section,
                                behavior_section,
                                security_section,
                                ft.Container(height=16),
                                ft.Row(
                                    controls=[save_button, reset_button],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    spacing=16,
                                ),
                            ],
                            scroll=ft.ScrollMode.AUTO,
                            spacing=16,
                        ),
                        expand=True,
                    ),
                ],
                expand=True,
            ),
            padding=20,
            bgcolor="#0f0f1a",
            expand=True,
        )
    
    def _build_section(self, title: str, controls: list) -> ft.Container:
        """Build a settings section."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        title,
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color="#888888",
                    ),
                    ft.Container(height=8),
                    *controls,
                ],
                spacing=12,
            ),
            padding=16,
            bgcolor="#1e1e2e",
            border_radius=12,
            border=ft.border.all(1, "#3d3d5c"),
        )
    
    def _build_security_section(self) -> ft.Container:
        """Build the security information section."""
        status = get_security_status()
        
        items = [
            self._build_security_item(
                "Game Detection",
                "psutil" if status['psutil_available'] else "Fallback mode",
                status['psutil_available'],
            ),
            self._build_security_item(
                "Payload Signing",
                "Enabled" if status['secret_configured'] else "Development mode",
                status['secret_configured'],
            ),
            self._build_security_item(
                "Anti-Cheat",
                "Active - only submits when game running",
                True,
            ),
        ]
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.SECURITY, color="#7c3aed", size=18),
                            ft.Text(
                                "Security",
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color="#888888",
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Container(height=8),
                    *items,
                ],
                spacing=8,
            ),
            padding=16,
            bgcolor="#1e1e2e",
            border_radius=12,
            border=ft.border.all(1, "#3d3d5c"),
        )
    
    def _build_security_item(self, label: str, value: str, is_good: bool) -> ft.Row:
        """Build a security status item."""
        return ft.Row(
            controls=[
                ft.Icon(
                    ft.Icons.CHECK_CIRCLE if is_good else ft.Icons.WARNING,
                    color="#51cf66" if is_good else "#ffd43b",
                    size=16,
                ),
                ft.Text(label, size=13, color="#ffffff"),
                ft.Container(expand=True),
                ft.Text(value, size=12, color="#888888"),
            ],
            spacing=8,
        )
    
    def _build_switch_row(
        self,
        title: str,
        subtitle: str,
        switch: ft.Switch,
    ) -> ft.Row:
        """Build a row with a switch control."""
        return ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        ft.Text(title, size=14, color="#ffffff"),
                        ft.Text(subtitle, size=12, color="#666666"),
                    ],
                    spacing=2,
                    expand=True,
                ),
                switch,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
    
    async def _browse_log_file(self, e):
        """Open file picker for log file."""
        file_picker = ft.FilePicker(
            on_result=self._on_file_picked,
        )
        self.page.overlay.append(file_picker)
        self.page.update()
        file_picker.pick_files(
            dialog_title="Select ACE Log File",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["txt", "log"],
        )
    
    def _on_file_picked(self, e: ft.FilePickerResultEvent):
        """Handle file picker result."""
        if e.files and len(e.files) > 0:
            self._log_path_field.value = e.files[0].path
            self._log_path_field.update()
    
    async def _test_connection(self, e):
        """Test server connection."""
        self._connection_status.value = "Testing..."
        self._connection_status.color = "#ffd43b"
        self._connection_status.update()
        
        if self.on_test_connection:
            success, message = await self.on_test_connection(
                self._server_url_field.value
            )
            if success:
                self._connection_status.value = "Connected"
                self._connection_status.color = "#51cf66"
            else:
                self._connection_status.value = f"{message}"
                self._connection_status.color = "#ff6b6b"
            self._connection_status.update()
    
    def _save_settings(self, e):
        """Save current settings."""
        # Update config from form fields
        self.config.log_path = self._log_path_field.value or DEFAULT_LOG_PATH
        self.config.server_url = self._server_url_field.value or DEFAULT_SERVER_URL
        
        self.config.auto_submit = self._auto_submit_switch.value
        self.config.submit_invalid_laps = self._submit_invalid_switch.value
        self.config.minimize_to_tray = self._minimize_to_tray_switch.value
        self.config.start_minimized = self._start_minimized_switch.value
        
        if self.on_save:
            self.on_save(self.config)
        
        # Show success feedback
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Settings saved!", color="#ffffff"),
            bgcolor="#51cf66",
        )
        self.page.snack_bar.open = True
        self.page.update()
    
    def _reset_settings(self, e):
        """Reset settings to defaults."""
        self._log_path_field.value = DEFAULT_LOG_PATH
        self._server_url_field.value = DEFAULT_SERVER_URL
        self._auto_submit_switch.value = True
        self._submit_invalid_switch.value = False
        self._minimize_to_tray_switch.value = True
        self._start_minimized_switch.value = False
        
        self._log_path_field.update()
        self._server_url_field.update()
        self._auto_submit_switch.update()
        self._submit_invalid_switch.update()
        self._minimize_to_tray_switch.update()
        self._start_minimized_switch.update()
    
    def update_config(self, config: AppConfig):
        """Update form with new config."""
        self.config = config
        self._log_path_field.value = config.log_path
        self._server_url_field.value = config.server_url
        self._auto_submit_switch.value = config.auto_submit
        self._submit_invalid_switch.value = config.submit_invalid_laps
        self._minimize_to_tray_switch.value = config.minimize_to_tray
        self._start_minimized_switch.value = config.start_minimized
        self.update()
