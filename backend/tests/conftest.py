import os
import threading
import time

import pytest

# The app's lifespan starts an hourly Census refresh loop; tests must never
# reach the live API (phase-4 rule: recorded fixtures only in CI).
os.environ["CENSUS_AUTO_REFRESH"] = "0"


HEARTBEAT_S = 15
_phase_lock = threading.Lock()
_phase = {"label": None, "started": 0.0, "next_report": HEARTBEAT_S}
_heartbeat_stop = threading.Event()
_heartbeat_thread = None
_heartbeat_config = None


def _set_phase(label):
    with _phase_lock:
        _phase.update(label=label, started=time.monotonic(),
                      next_report=HEARTBEAT_S)


def _clear_phase():
    with _phase_lock:
        _phase["label"] = None


def _heartbeat_loop():
    """Keep long collection/test phases visibly alive in quiet-mode runs."""
    while not _heartbeat_stop.wait(0.5):
        with _phase_lock:
            label = _phase["label"]
            elapsed = time.monotonic() - _phase["started"]
            report_at = _phase["next_report"]
            if label is None or elapsed < report_at:
                continue
            _phase["next_report"] += HEARTBEAT_S

        reporter = _heartbeat_config.pluginmanager.get_plugin("terminalreporter")
        capture = _heartbeat_config.pluginmanager.get_plugin("capturemanager")
        if reporter is None:
            continue
        if capture is not None:
            capture.suspend_global_capture(in_=False)
        try:
            reporter.write_line(f"[tests] still {label} ({report_at}s)")
        finally:
            if capture is not None:
                capture.resume_global_capture()


def pytest_sessionstart(session):
    global _heartbeat_config, _heartbeat_thread
    _heartbeat_config = session.config
    _heartbeat_stop.clear()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name="pytest-heartbeat", daemon=True)
    _heartbeat_thread.start()


def pytest_sessionfinish(session, exitstatus):
    _clear_phase()
    _heartbeat_stop.set()
    if _heartbeat_thread is not None:
        _heartbeat_thread.join()


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session):
    _set_phase("collecting")
    try:
        yield
    finally:
        _clear_phase()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _set_phase(f"running {item.nodeid}")
    try:
        yield
    finally:
        _clear_phase()
