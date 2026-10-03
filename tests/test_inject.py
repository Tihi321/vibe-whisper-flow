from vibeflow import inject
from vibeflow.config import OutputConfig


def _patch(monkeypatch, clipboard="previous"):
    calls = []
    monkeypatch.setattr(inject, "get_clipboard_text", lambda: calls.append(("get",)) or clipboard)
    monkeypatch.setattr(inject, "set_clipboard_text", lambda t: calls.append(("set", t)))
    monkeypatch.setattr(inject, "send_ctrl_v", lambda: calls.append(("paste",)))
    monkeypatch.setattr(inject, "type_text", lambda t: calls.append(("type", t)))
    monkeypatch.setattr(inject.time, "sleep", lambda s: calls.append(("sleep", s)))
    return calls


def test_type_mode(monkeypatch):
    calls = _patch(monkeypatch)
    inject.insert_text("hello", OutputConfig(mode="type"))
    assert calls == [("type", "hello")]


def test_paste_mode_no_restore(monkeypatch):
    calls = _patch(monkeypatch)
    inject.insert_text("hello", OutputConfig(mode="paste", restore_clipboard=False,
                                             paste_delay_ms=60))
    assert [c[0] for c in calls] == ["set", "sleep", "paste"]
    assert calls[0] == ("set", "hello")
    assert calls[1] == ("sleep", 0.06)


def test_paste_mode_restore(monkeypatch):
    calls = _patch(monkeypatch)
    inject.insert_text("hello", OutputConfig(mode="paste", restore_clipboard=True,
                                             paste_delay_ms=50))
    assert calls == [
        ("get",),
        ("set", "hello"),
        ("sleep", 0.05),
        ("paste",),
        ("sleep", 0.3),
        ("set", "previous"),
    ]


def test_paste_restore_skipped_when_no_previous_text(monkeypatch):
    calls = _patch(monkeypatch, clipboard=None)
    inject.insert_text("hello", OutputConfig(mode="paste", restore_clipboard=True))
    assert [c for c in calls if c[0] == "set"] == [("set", "hello")]


def test_empty_text_is_noop(monkeypatch):
    calls = _patch(monkeypatch)
    inject.insert_text("", OutputConfig())
    assert calls == []


def test_chord_wait_called(monkeypatch):
    calls = _patch(monkeypatch)
    import vibeflow.hotkeys as hk

    monkeypatch.setattr(hk, "wait_for_chord_release",
                        lambda chord, timeout=1.0: calls.append(("wait", chord, timeout)))
    inject.insert_text("x", OutputConfig(mode="type"), chord="CHORD")
    assert calls[0] == ("wait", "CHORD", 1.0)
    assert calls[1] == ("type", "x")
