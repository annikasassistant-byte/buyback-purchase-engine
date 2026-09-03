"""One place to configure stdlib logging for the CLI.

Library code (``purchase_engine.*``) only ever calls ``logging.getLogger(__name__)``
and never configures handlers - that is the application's job (here, the CLI).
"""

from __future__ import annotations

import logging

_LOGGER_NAME = "purchase_engine"


def configure_logging(verbosity: int = 0) -> None:
    """Attach a single stderr handler to the ``purchase_engine`` logger.

    ``verbosity`` 0 -> WARNING, 1 -> INFO, 2+ -> DEBUG.
    """
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
