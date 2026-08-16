"""Current-Flet helpers for transient user feedback."""

import flet as ft


def show_snackbar(page: ft.Page, message: str, bgcolor: str) -> ft.SnackBar:
    """Show a snackbar using Flet's dialog lifecycle API."""
    snack = ft.SnackBar(content=ft.Text(message), bgcolor=bgcolor)
    page.show_dialog(snack)
    return snack
