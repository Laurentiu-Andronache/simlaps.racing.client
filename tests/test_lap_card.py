"""Regression tests for the lap-card component."""

from unittest.mock import PropertyMock, patch

from src.models import LapData, SessionData
from src.ui.components.lap_card import LapCard, LapCardData, LapCardStatus


def test_flet_initialization_preserves_data_for_status_updates() -> None:
    """The Flet base constructor must not replace ``LapCard.data`` with None."""
    data = LapCardData(
        session=SessionData(track="red_bull_ring", car="mazda_mx5_nd_cup"),
        lap=LapData(
            lap_number=1,
            physics_lap_number=1,
            lap_time_ms=128028,
            lap_time_str="02:08.028",
        ),
        lap_number=1,
        status=LapCardStatus.SUBMITTING,
    )

    with patch.object(LapCard, "page", new_callable=PropertyMock, return_value=None):
        card = LapCard(data)
        card.update_status(LapCardStatus.FAILED, "offline")

    assert card.data is data
    assert card.data.status == LapCardStatus.FAILED
    assert card.data.error_message == "offline"
