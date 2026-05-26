from src.core.log_parser import LapData, LogParser, SessionData


PLAYER_CAR_ID = "4576ad130bff4e0e-530795509c9149a7"


def make_parser(with_completed_lap: bool = False) -> LogParser:
    parser = LogParser()
    parser.context.car_uuid = PLAYER_CAR_ID
    parser.context.player_car_uuids.add(PLAYER_CAR_ID)
    parser.current_session = SessionData(car_uuid=PLAYER_CAR_ID)
    if with_completed_lap:
        parser.current_session.laps.append(
            LapData(
                lap_number=1,
                physics_lap_number=1,
                lap_time_ms=100000,
                lap_time_str="1:40.000",
            )
        )
    return parser


def test_prelap_full_batch_overwrites_previous_mixed_state():
    parser = make_parser()

    parser._handle_compound_v2(
        "[2026-04-02 23:22:17.035] [physics] [info] setCompound Tyre: 0 compound name: HC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:22:17.035] [physics] [info] setCompound Tyre: 1 compound name: SC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:22:17.035] [physics] [info] setCompound Tyre: 2 compound name: HC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:22:17.035] [physics] [info] setCompound Tyre: 3 compound name: SC"
    )
    parser._flush_pending_compound_batch()

    assert parser.context.tyre.compound_name == "Mixed (HC/SC)"

    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [physics] [info] setCompound Tyre: 0 compound name: HC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [physics] [info] setCompound Tyre: 1 compound name: HC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [physics] [info] setCompound Tyre: 2 compound name: HC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [physics] [info] setCompound Tyre: 3 compound name: HC"
    )
    parser._flush_pending_compound_batch()

    assert parser.context.tyre.compound_name == "HC"


def test_player_confirmed_partial_update_resolves_to_single_compound():
    parser = make_parser()
    parser.context.tyre.set(0, "HC")
    parser.context.tyre.set(1, "SC")
    parser.context.tyre.set(2, "HC")
    parser.context.tyre.set(3, "SC")

    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [physics] [info] setCompound Tyre: 1 compound name: HC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [platformCore] [info] CarId: 4576ad130bff4e0e-530795509c9149a7 Tyre: 1 compound: 2"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [physics] [info] setCompound Tyre: 3 compound name: HC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:33:05.182] [platformCore] [info] CarId: 4576ad130bff4e0e-530795509c9149a7 Tyre: 3 compound: 2"
    )
    parser._flush_pending_compound_batch()

    assert parser.context.tyre.compound_name == "HC"


def test_unconfirmed_batch_is_ignored_after_laps_have_started():
    parser = make_parser(with_completed_lap=True)
    parser.context.tyre.set_all("HC")

    parser._handle_compound_v2(
        "[2026-04-02 23:40:00.000] [physics] [info] setCompound Tyre: 0 compound name: SC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:40:00.000] [physics] [info] setCompound Tyre: 1 compound name: SC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:40:00.000] [physics] [info] setCompound Tyre: 2 compound name: SC"
    )
    parser._handle_compound_v2(
        "[2026-04-02 23:40:00.000] [physics] [info] setCompound Tyre: 3 compound name: SC"
    )
    parser._flush_pending_compound_batch()

    assert parser.context.tyre.compound_name == "HC"


def test_loading_compound_falls_back_to_context_car_uuid_without_teleport():
    """Practice sessions often lack CarTeleportCompleted; _last_car_uuid is None
    but the player car UUID is already known from connect lines."""
    parser = make_parser()
    parser._last_car_uuid = None  # simulate missing teleport

    parser._handle_compound_v2(
        "[2026-04-02 23:22:17.035] [physics] [info] LOADING TYRE COMPOUND Slicks (S)"
    )

    assert parser.context.tyre.compound_name == "Slicks (S)"
