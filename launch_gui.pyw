"""Launcher for SWPPP AutoFill GUI — .pyw runs with pythonw (no console)."""

import os
import site
import sys
from pathlib import Path

# Activate venv site-packages when launched via base pythonw.exe
_venv = Path(__file__).resolve().parent / ".venv"
_site_pkgs = _venv / "Lib" / "site-packages"
if _site_pkgs.is_dir() and str(_site_pkgs) not in sys.path:
    site.addsitedir(str(_site_pkgs))

# Ensure project root is on sys.path
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.ui_gui.main import main

main()
