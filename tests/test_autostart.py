import sys
from pathlib import Path

from vibeflow import autostart
from vibeflow.config import ROOT


def test_launch_command_format():
    cmd = autostart.launch_command()
    assert cmd.startswith('"')
    assert cmd.endswith(f'"{ROOT / "run.pyw"}"')
    exe = cmd.split('" "')[0].strip('"')
    assert Path(exe).name.lower() in ("pythonw.exe", Path(sys.executable).name.lower())


def test_app_key():
    assert autostart.APP_KEY == "VibeFlow"
