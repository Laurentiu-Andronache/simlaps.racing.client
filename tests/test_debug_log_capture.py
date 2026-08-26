import re

from src.ui.components import debug_logs
from src.utils.structured_logger import Component, StructuredLogger


def test_structured_and_raw_stream_events_are_captured_once(capsys):
    """Structured events bypass wrapped streams; ordinary writes still mirror."""
    debug_logs.stop_log_capture()
    debug_logs._log_capture.clear_logs()
    logger = StructuredLogger()

    assert debug_logs.start_log_capture() is True
    assert debug_logs.start_log_capture() is False
    try:
        logger.debug(Component.APP, "dedup debug")
        logger.info(Component.APP, "dedup info")
        logger.warning(Component.APP, "dedup warning")
        logger.error(Component.APP, "dedup error")
        logger.critical(Component.APP, "dedup critical")
        print("dedup raw stdout")
        import sys

        print("dedup raw stderr", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()

        captured_logs = debug_logs._log_capture.get_logs().splitlines()
    finally:
        assert debug_logs.stop_log_capture() is True
        assert debug_logs.stop_log_capture() is False

    expected_messages = [
        "dedup debug",
        "dedup info",
        "dedup warning",
        "dedup error",
        "dedup critical",
        "dedup raw stdout",
        "dedup raw stderr",
    ]
    for message in expected_messages:
        matching = [line for line in captured_logs if message in line]
        assert len(matching) == 1, (message, captured_logs)
        assert len(re.findall(r"\[\d{2}:\d{2}:\d{2}\]", matching[0])) == 1

    # The wrapper was restored, allowing pytest's normal capture fixture to
    # retain the console output emitted by the structured warning/error path.
    console = capsys.readouterr()
    assert "dedup warning" in console.out
    assert "dedup error" in console.err
