"""Shared pytest fixtures.

Warms up the structured logger once per session. The first ``log_debug`` call
lazily imports the UI module (``src.ui.components.debug_logs``), which costs
~0.5s. In timing-sensitive async tests (e.g. live log-tailing with a short
``asyncio.wait_for`` timeout) that one-time cost runs synchronously inside the
code under test and starves the event loop, preventing sibling tasks from
running within the test window. Triggering it up front makes those tests
deterministic.
"""

import os

os.environ.setdefault(
    "APP_SECRET",
    "0000000000000000000000000000000000000000000000000000000000000000",
)

import pytest

from src.utils.structured_logger import log_debug, Component


@pytest.fixture(scope="session", autouse=True)
def _warm_up_structured_logger():
    """Pay the one-time lazy-import cost of the logger before tests run."""
    log_debug(Component.LOG_PARSER, "[conftest] logger warm-up")
