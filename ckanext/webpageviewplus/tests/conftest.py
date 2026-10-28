import logging

import pytest


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.events = []

    def emit(self, record):
        if hasattr(record, "webpageviewplus"):
            self.events.append(record.webpageviewplus)


@pytest.fixture
def events_log():
    """The extension's structured events, as dicts, from a handler on the
    events logger itself (caplog's handler is replaced between test phases).
    It's re-enabled too: tests that load CKAN's logging config disable the
    loggers that already exist."""
    logger = logging.getLogger("ckanext.webpageviewplus.events")
    handler = _Collect()
    old = logger.level, logger.disabled
    logger.setLevel(logging.INFO)
    logger.disabled = False
    logger.addHandler(handler)
    yield lambda: list(handler.events)
    logger.removeHandler(handler)
    logger.level, logger.disabled = old
