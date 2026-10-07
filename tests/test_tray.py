from vibeflow.tray import Tray, TrayHooks, make_icon


def test_make_icon_size_and_mode():
    img = make_icon("#e8e8e8")
    assert img.size == (64, 64)
    assert img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0          # transparent corner
    assert img.getpixel((32, 20))[3] == 255      # capsule is opaque


def test_hooks_construct():
    noop = lambda *a, **k: None
    hooks = TrayHooks(
        is_enabled=lambda: True, set_enabled=noop,
        current_backend=lambda: "local", set_backend=noop,
        whisper_models=lambda: [], current_whisper_model=lambda: "",
        set_whisper_model=noop,
        cleanup_models=lambda: [], current_cleanup_model=lambda: None,
        set_cleanup_model=noop,
        is_autostart=lambda: False, set_autostart=noop,
        open_settings=noop, open_config=noop, open_logs=noop, reload_config=noop, quit=noop,
    )
    assert hooks.is_enabled() is True


def test_update_menu_before_start_does_not_raise():
    noop = lambda *a, **k: None
    hooks = TrayHooks(
        is_enabled=lambda: True, set_enabled=noop,
        current_backend=lambda: "local", set_backend=noop,
        whisper_models=lambda: [], current_whisper_model=lambda: "",
        set_whisper_model=noop,
        cleanup_models=lambda: [], current_cleanup_model=lambda: None,
        set_cleanup_model=noop,
        is_autostart=lambda: False, set_autostart=noop,
        open_settings=noop, open_config=noop, open_logs=noop, reload_config=noop, quit=noop,
    )
    Tray(hooks).update_menu()
