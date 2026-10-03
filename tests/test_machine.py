import pytest

from vibeflow.app import DictationMachine, Mode, State
from vibeflow.config import HotkeyConfig


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Sched:
    def __init__(self):
        self.items = []

    def schedule(self, delay, fn):
        h = {"delay": delay, "fn": fn, "cancelled": False, "fired": False}
        self.items.append(h)
        return h

    def cancel(self, h):
        h["cancelled"] = True

    def active(self):
        return [h for h in self.items if not h["cancelled"] and not h["fired"]]

    def fire(self, delay):
        """Fire the latest unfired timer with this delay (even if cancelled, like a late thread)."""
        h = [h for h in self.items if h["delay"] == pytest.approx(delay) and not h["fired"]][-1]
        h["fired"] = True
        h["fn"]()


class Actions:
    def __init__(self):
        self.calls = []
        self.start_ok = True
        self.states = []

    def start_recording(self):
        self.calls.append("start")
        return self.start_ok

    def stop_and_process(self, mode):
        self.calls.append(("process", mode))

    def discard_recording(self):
        self.calls.append("discard")

    def cancel_processing(self):
        self.calls.append("cancel")

    def state_changed(self, state, mode):
        self.states.append((state, mode))


@pytest.fixture
def env():
    clock, sched, actions = Clock(), Sched(), Actions()
    cfg = HotkeyConfig(tap_ms=300, double_tap_ms=400, max_recording_seconds=300,
                       min_recording_seconds=0.6)
    m = DictationMachine(cfg, actions, clock=clock, schedule=sched.schedule, cancel=sched.cancel)
    return m, clock, sched, actions


def test_hold_release_processes(env):
    m, clock, sched, a = env
    m.chord_down()
    assert m.state == State.REC_HOLD and m.mode == Mode.HOLD
    assert a.calls == ["start"]
    clock.advance(1.0)
    assert m.recording_seconds() == pytest.approx(1.0)
    m.chord_up()
    assert m.state == State.PROCESSING
    assert a.calls[-1] == ("process", Mode.HOLD)
    assert not sched.active()  # max timer cancelled
    m.processing_done()
    assert m.state == State.IDLE and m.mode is None
    assert a.states[-1] == (State.IDLE, None)


def test_every_transition_notifies(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(1)
    m.chord_up()
    m.processing_done()
    assert [s for s, _ in a.states] == [State.REC_HOLD, State.PROCESSING, State.IDLE]


def test_start_failure_stays_idle(env):
    m, clock, sched, a = env
    a.start_ok = False
    m.chord_down()
    assert m.state == State.IDLE
    assert a.states == []
    assert not sched.active()


def test_short_press_enters_tapwait_then_discards(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(0.1)
    m.chord_up()
    assert m.state == State.REC_TAPWAIT
    assert any(h["delay"] == pytest.approx(0.4) for h in sched.active())
    clock.advance(0.4)
    sched.fire(0.4)
    assert m.state == State.IDLE  # 0.5 s < min 0.6
    assert "discard" in a.calls
    assert not sched.active()


def test_tapwait_timeout_processes_if_long_enough(env):
    m, clock, sched, a = env
    m.cfg.min_recording_seconds = 0.3
    m.chord_down()
    clock.advance(0.2)
    m.chord_up()
    clock.advance(0.4)
    sched.fire(0.4)
    assert m.state == State.PROCESSING
    assert a.calls[-1] == ("process", Mode.HOLD)


def test_double_tap_to_handsfree_and_tap_to_stop(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(0.1)
    m.chord_up()
    clock.advance(0.2)
    m.chord_down()
    assert m.state == State.REC_HANDSFREE and m.mode == Mode.HANDSFREE
    clock.advance(0.1)
    m.chord_up()  # ignored
    assert m.state == State.REC_HANDSFREE
    assert not any(h["delay"] == pytest.approx(0.4) for h in sched.active())
    clock.advance(10)
    m.chord_down()  # tap to stop
    assert m.state == State.PROCESSING
    assert a.calls[-1] == ("process", Mode.HANDSFREE)
    m.chord_up()  # ignored
    assert m.state == State.PROCESSING
    m.processing_done()
    assert m.state == State.IDLE
    # ignore flag consumed; next hold works normally
    m.chord_down()
    clock.advance(1)
    m.chord_up()
    assert m.state == State.PROCESSING


def test_stale_double_tap_timer_after_handsfree(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(0.1)
    m.chord_up()
    clock.advance(0.1)
    m.chord_down()
    m.chord_up()
    sched.fire(0.4)  # stale firing must be harmless
    assert m.state == State.REC_HANDSFREE


def test_max_timer_hold(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(300)
    sched.fire(300.0)
    assert m.state == State.PROCESSING
    assert a.calls[-1] == ("process", Mode.HOLD)
    m.chord_up()  # ignored
    assert m.state == State.PROCESSING


def test_max_timer_handsfree(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(0.1)
    m.chord_up()
    m.chord_down()
    clock.advance(300)
    sched.fire(300.0)
    assert m.state == State.PROCESSING
    assert a.calls[-1] == ("process", Mode.HANDSFREE)


def test_max_timer_tapwait(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(0.1)
    m.chord_up()
    sched.fire(300.0)
    assert m.state == State.PROCESSING


@pytest.mark.parametrize("setup", ["hold", "tapwait", "handsfree"])
def test_escape_in_recording_discards(env, setup):
    m, clock, sched, a = env
    m.chord_down()
    if setup != "hold":
        clock.advance(0.1)
        m.chord_up()
    if setup == "handsfree":
        m.chord_down()
    m.escape()
    assert m.state == State.IDLE
    assert "discard" in a.calls
    assert not sched.active()


def test_escape_in_processing_cancels(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(1)
    m.chord_up()
    m.escape()
    assert m.state == State.IDLE
    assert a.calls[-1] == "cancel"
    m.processing_done()  # late done is harmless
    assert m.state == State.IDLE


def test_escape_idle_noop(env):
    m, clock, sched, a = env
    m.escape()
    assert m.state == State.IDLE and a.calls == []


def test_chord_down_in_processing_ignored(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(1)
    m.chord_up()
    n = len(a.calls)
    m.chord_down()
    m.chord_up()
    assert m.state == State.PROCESSING and len(a.calls) == n


def test_disable_while_recording_discards(env):
    m, clock, sched, a = env
    m.chord_down()
    m.set_enabled(False)
    assert m.state == State.IDLE and m.enabled is False
    assert "discard" in a.calls
    a.calls.clear()
    m.chord_down()
    assert m.state == State.IDLE and a.calls == []
    m.set_enabled(True)
    m.chord_down()
    assert m.state == State.REC_HOLD


def test_key_repeat_in_hold_ignored(env):
    m, clock, sched, a = env
    m.chord_down()
    m.chord_down()
    assert a.calls.count("start") == 1 and m.state == State.REC_HOLD


def test_hold_just_over_tap_ms_is_hold(env):
    m, clock, sched, a = env
    m.chord_down()
    clock.advance(0.31)
    m.chord_up()
    assert m.state == State.PROCESSING


def test_default_timers_construct():
    m = DictationMachine(HotkeyConfig(), Actions())
    m.chord_down()
    assert m.state == State.REC_HOLD
    m.escape()  # cancels real threading.Timer
    assert m.state == State.IDLE


def test_cleanup_breaker():
    from vibeflow.app import CleanupBreaker
    clock = Clock()
    b = CleanupBreaker(30.0, clock=clock)
    assert b.allowed()
    assert b.note_failure() is True   # first failure logs
    assert not b.allowed()
    clock.advance(10)
    assert b.note_failure() is False  # already open: no repeated log
    clock.advance(29)
    assert not b.allowed()
    clock.advance(2)
    assert b.allowed()
    b.note_failure()
    b.reset()
    assert b.allowed()


def test_is_silent():
    from types import SimpleNamespace
    from vibeflow.app import is_silent
    assert is_silent(SimpleNamespace(peak=0.005), 0.01)
    assert not is_silent(SimpleNamespace(peak=0.5), 0.01)
    assert not is_silent(SimpleNamespace(), 0.01)  # no peak info -> not silent
