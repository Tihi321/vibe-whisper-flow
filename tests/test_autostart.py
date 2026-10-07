import sys
from pathlib import Path

from vibeflow import autostart
from vibeflow.config import ROOT


def test_launch_command_format():
    cmd = autostart.launch_command()
    assert cmd.startswith('"')
    assert cmd.endswith(f'"{ROOT / "run.pyw"}" --hidden')
    exe = cmd.split('" "')[0].strip('"')
    assert Path(exe).name.lower() in ("pythonw.exe", Path(sys.executable).name.lower())


def test_launch_command_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\VibeFlow\VibeFlow.exe")
    assert autostart.launch_command() == r'"C:\Apps\VibeFlow\VibeFlow.exe" --hidden'


def test_app_key():
    assert autostart.APP_KEY == "VibeFlow"


def test_sync_rewrites_stale_command(monkeypatch):
    written = []
    monkeypatch.setattr(autostart, "current_command", lambda: '"old" "x"')
    monkeypatch.setattr(autostart, "enable", lambda: written.append(1))
    assert autostart.sync() is True
    assert written == [1]


def test_sync_noop_when_current_or_disabled(monkeypatch):
    written = []
    monkeypatch.setattr(autostart, "enable", lambda: written.append(1))
    monkeypatch.setattr(autostart, "current_command", lambda: autostart.launch_command())
    assert autostart.sync() is False
    monkeypatch.setattr(autostart, "current_command", lambda: None)
    assert autostart.sync() is False
    assert written == []


def test_sync_never_raises(monkeypatch):
    monkeypatch.setattr(autostart, "current_command", lambda: "stale")

    def boom():
        raise OSError("denied")

    monkeypatch.setattr(autostart, "enable", boom)
    assert autostart.sync() is False
