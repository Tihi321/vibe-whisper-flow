# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the portable VibeFlow build (one-folder).
# Build via scripts\build.ps1 (generates build\vibeflow.ico first).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

# SPECPATH is the folder containing this spec (packaging/); the repo root is its parent.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

datas = [(os.path.join(ROOT, "config.example.toml"), ".")]
datas += collect_data_files("sv_ttk")
datas += collect_data_files("onnx_asr")  # preprocessor graphs
# onnx_asr reads its own version via importlib.metadata on import; without the dist-info the
# frozen app fails with PackageNotFoundError.
datas += copy_metadata("onnx-asr") + copy_metadata("onnxruntime")
binaries = collect_dynamic_libs("onnxruntime")  # Parakeet backend (onnx-asr)
binaries += collect_dynamic_libs("av")  # FFmpeg DLLs for decoding audio/video files

hiddenimports = [
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "pystray._win32",
    # imported lazily from vibeflow.app
    "vibeflow.settings_ui",
    "vibeflow.whisper_setup",
    "vibeflow.parakeet",
    "vibeflow.filetranscribe",
    "av",
    "onnx_asr",
    "vibeflow.single_instance",
]

a = Analysis(
    [os.path.join(ROOT, "run.pyw")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "tests", "pip", "setuptools", "pyinstaller", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VibeFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, "build", "vibeflow.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VibeFlow",
)
