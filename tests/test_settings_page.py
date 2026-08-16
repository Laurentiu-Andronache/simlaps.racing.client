"""Behavior tests for the Settings page using real Flet controls."""

from unittest.mock import MagicMock, PropertyMock, patch

from src.ui.pages.settings import SettingsPage
from src.utils.config import AppConfig


def test_save_enables_telemetry_without_mutating_active_config() -> None:
    active_config = AppConfig(telemetry_enabled=False)
    saved = []
    page = SettingsPage(config=active_config, on_save=saved.append)
    page._telemetry_enabled_switch.value = True

    fake_page = MagicMock()
    with (
        patch.object(SettingsPage, "page", new_callable=PropertyMock, return_value=fake_page),
        patch("src.ui.pages.settings.show_snackbar") as feedback,
    ):
        page._save_settings(None)

    assert active_config.telemetry_enabled is False
    assert len(saved) == 1
    assert saved[0].telemetry_enabled is True
    assert page.config is saved[0]
    feedback.assert_called_once_with(fake_page, "Settings saved!", "#51cf66")


def test_reset_keeps_active_page_config_when_apply_fails() -> None:
    active_config = AppConfig(telemetry_enabled=True)

    def fail_save(_config: AppConfig) -> None:
        raise OSError("disk full")

    page = SettingsPage(config=active_config, on_save=fail_save)
    fake_page = MagicMock()
    with (
        patch.object(SettingsPage, "page", new_callable=PropertyMock, return_value=fake_page),
        patch("src.ui.pages.settings.show_snackbar") as feedback,
    ):
        page._reset_settings(None)

    assert page.config is active_config
    assert page._telemetry_enabled_switch.value is True
    feedback.assert_called_once_with(
        fake_page,
        "Could not reset settings: disk full",
        "#ff6b6b",
    )
