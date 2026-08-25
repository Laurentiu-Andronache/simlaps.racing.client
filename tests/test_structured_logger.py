from unittest.mock import patch

from src.utils.structured_logger import Component, StructuredLogger


def _raise_explicit_error() -> None:
    raise ValueError("explicit traceback")


def test_exception_formats_supplied_exception_traceback_outside_except_block():
    logger = StructuredLogger()
    try:
        _raise_explicit_error()
    except ValueError as exception:
        saved_exception = exception

    with patch.object(logger, "error") as log_error:
        with patch("src.ui.components.debug_logs.add_debug_log") as add_debug_log:
            logger.exception(Component.APP, "Task failed", saved_exception)

    log_error.assert_called_once_with(
        Component.APP,
        "Task failed: ValueError: explicit traceback",
    )
    traceback_text = "\n".join(call.args[0] for call in add_debug_log.call_args_list)
    assert "_raise_explicit_error" in traceback_text
    assert "ValueError: explicit traceback" in traceback_text
    assert "NoneType: None" not in traceback_text
