# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SWPPP AutoFill GUI

import os
from pathlib import Path

block_cipher = None
root = Path(SPECPATH)

a = Analysis(
    [str(root / "app" / "ui_gui" / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "assets" / "template.pdf"), "assets"),
        (str(root / "app" / "core" / "odot_mapping.yaml"), "app/core"),
    ],
    hiddenimports=[
        "app",
        "app.core",
        "app.core.config_manager",
        "app.core.dates",
        "app.core.fill",
        "app.core.mesonet",
        "app.core.mesonet_stations",
        "app.core.model",
        "app.core.pdf_fields",
        "app.core.rain_fill",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SWPPP AutoFill",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window — Tkinter GUI only
    icon=None,
)
