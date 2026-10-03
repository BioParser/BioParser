import signal
from threading import Event

import pytest

from bioparser.worker.parse.runtime import install_shutdown


@pytest.fixture
def restore_signals() -> object:
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_sets_stop_event(restore_signals: None, sig: signal.Signals) -> None:
    stop = Event()
    install_shutdown(stop)
    assert not stop.is_set()

    signal.raise_signal(sig)

    assert stop.is_set()
