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




def test_loading_compound_does_not_override_resolved_player_compound():
    parser = make_parser()

    parser.context.tyre.set_all("SC")
    parser._last_car_uuid = PLAYER_CAR_ID

    parser._handle_compound_v2(
        "[2026-06-03 23:21:08.329] [physics] [info] LOADING TYRE COMPOUND Road (RD)"
    )

    assert parser.context.tyre.compound_name == "SC"
def test_ai_prelap_compound_batch_does_not_replace_player_tyres():
    parser = make_parser()
    ai_car_id = "412d4e4b881874e9-a398f42b7df830b2"

    parser._process_line(
        f"[2026-06-03 23:21:00.451] [gameplay] [info] "
        f"FUEL car {PLAYER_CAR_ID} setup with 30 L"
    )
    for pos in range(4):
        parser._process_line(
            f"[2026-06-03 23:21:00.451] [physics] [info] "
            f"setCompound Tyre: {pos} compound name: SC"
        )

    parser._process_line(
        f"[2026-06-03 23:21:08.325] [gameplay] [info] "
        f"FUEL car {ai_car_id} setup with 30 L"
    )
    for pos in range(4):
        parser._process_line(
            f"[2026-06-03 23:21:08.329] [physics] [info] "
            f"setCompound Tyre: {pos} compound name: RD"
        )

    parser._flush_pending_compound_batch()

    assert parser.context.tyre.compound_name == "SC"
