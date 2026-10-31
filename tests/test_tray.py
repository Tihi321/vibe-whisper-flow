from vibeflow import tray as tray_mod
from vibeflow.tray import Tray, TrayHooks, make_icon


def _hooks(**overrides) -> TrayHooks:
    noop = lambda *a, **k: None  # noqa: E731
    base = dict(
        is_enabled=lambda: True, set_enabled=noop,
        current_backend=lambda: "local", set_backend=noop,
        whisper_models=lambda: [], current_whisper_model=lambda: "",
        set_whisper_model=noop,
        cleanup_models=lambda: [], current_cleanup_model=lambda: None,
        set_cleanup_model=noop,
        is_autostart=lambda: False, set_autostart=noop,
        open_settings=noop, open_config=noop, open_logs=noop, reload_config=noop, quit=noop,
    )
    base.update(overrides)
    return TrayHooks(**base)


def test_make_icon_size_and_mode():
    img = make_icon("#e8e8e8")
    assert img.size == (64, 64)
    assert img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0          # transparent corner
    assert img.getpixel((32, 20))[3] == 255      # capsule is opaque


def test_hooks_construct():
    hooks = _hooks()
    assert hooks.is_enabled() is True
    assert hooks.input_devices() == [] and hooks.current_input_device() == ""


def test_update_menu_before_start_does_not_raise():
    Tray(_hooks()).update_menu()


def _mic_submenu(**overrides):
    t = Tray(_hooks(**overrides))
    item = next(i for i in t._menu_items() if getattr(i, "text", "") == "Microphone")
    return t, list(item.submenu.items)


def _labels(items):
    return [i.text for i in items if i.text != "- - - -"]


def test_microphone_default_selected():
    _, items = _mic_submenu(input_devices=lambda: ["Mic A", "Mic B"],
                            default_input_device=lambda: "Mic A",
                            current_input_device=lambda: "")
    assert _labels(items)[:3] == ["Windows default (Mic A)", "Mic A", "Mic B"]
    assert [bool(i.checked) for i in items[:3]] == [True, False, False]
    assert _labels(items)[-2:] == ["Refresh list", "Test microphone…"]


def test_microphone_default_without_known_name():
    _, items = _mic_submenu(input_devices=lambda: ["Mic A"])
    assert items[0].text == "Windows default"


def test_microphone_named_and_legacy_substring_selected():
    _, items = _mic_submenu(input_devices=lambda: ["Mic A", "USB Mic B"],
                            current_input_device=lambda: "Mic A")
    assert [bool(i.checked) for i in items[:3]] == [False, True, False]
    _, items = _mic_submenu(input_devices=lambda: ["Mic A", "USB Mic B"],
                            current_input_device=lambda: "usb")
    assert [bool(i.checked) for i in items[:3]] == [False, False, True]


def test_microphone_missing_device_item():
    _, items = _mic_submenu(input_devices=lambda: ["Mic A"],
                            current_input_device=lambda: "Ghost")
    missing = next(i for i in items if i.text == "Ghost (not connected)")
    assert bool(missing.checked) is True and missing.enabled is False


def test_microphone_empty_list():
    _, items = _mic_submenu(input_devices=lambda: [])
    none = next(i for i in items if i.text == "No microphones found")
    assert none.enabled is False


def test_microphone_device_list_error_is_swallowed():
    def boom():
        raise RuntimeError("x")

    _, items = _mic_submenu(input_devices=boom)
    assert any(i.text == "No microphones found" for i in items)


def test_microphone_actions_call_hooks():
    picked, opened = [], []
    t, items = _mic_submenu(input_devices=lambda: ["Mic A"],
                            set_input_device=picked.append,
                            open_mic_test=lambda: opened.append(1))
    items[0](None)
    items[1](None)
    next(i for i in items if i.text.startswith("Test microphone"))(None)
    assert picked == ["", "Mic A"] and opened == [1]


def test_refresh_list_notifies_only_when_refused(monkeypatch):
    results = [False, True]
    t, items = _mic_submenu(refresh_input_devices=lambda: results.pop(0))
    notes = []
    monkeypatch.setattr(t, "notify", lambda title, msg: notes.append(msg))
    refresh = next(i for i in items if i.text == "Refresh list")
    refresh(None)
    assert len(notes) == 1 and "not rescanned" in notes[0]
    refresh(None)
    assert len(notes) == 1
    assert tray_mod  # module import kept for monkeypatch symmetry
