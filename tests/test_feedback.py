"""Tests for current-Flet transient feedback helpers."""

from unittest.mock import MagicMock

from src.ui.components.feedback import show_snackbar


def test_show_snackbar_uses_dialog_lifecycle() -> None:
    page = MagicMock()

    snack = show_snackbar(page, "Saved", "#51cf66")

    page.show_dialog.assert_called_once_with(snack)
    assert snack.content.value == "Saved"
    assert snack.bgcolor == "#51cf66"
