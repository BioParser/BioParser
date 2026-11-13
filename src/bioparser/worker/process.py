"""Process signals shared by the worker entrypoints."""

import logging
import signal
from threading import Event

LOGGER = logging.getLogger(__name__)


def install_shutdown(stop: Event) -> None:
    """Stop the consumer on SIGINT and SIGTERM. The queue joins in-flight work."""

    def _request_stop(signum: int, _frame: object) -> None:
        LOGGER.info(
            f"received {signal.Signals(signum).name}. Finishing the current job, then shutting down"
        )
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
