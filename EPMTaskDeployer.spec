# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: one-folder, windowed (no console), UPX off.
# Normally run via build.ps1:  .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean EPMTaskDeployer.spec
#
# Bundle layout (dist\EPMTaskDeployer\):
#   EPMTaskDeployer.exe            the application
#   _internal\                     Python runtime, libraries, config\, assets\, customtkinter themes/fonts
#   _internal\docs\user-guide\     USER_GUIDE.html, only when it exists at build time

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (SPECPATH is provided by PyInstaller)
APP_NAME = "EPMTaskDeployer"

# --- data files -------------------------------------------------------------------------------
datas = []
datas += collect_data_files("customtkinter")  # themes, fonts, icons used by the widgets
datas.append((os.path.join(ROOT, "config"), "config"))  # read-only JSON configuration
datas.append((os.path.join(ROOT, "assets", "app.ico"), "assets"))  # window icon

GUIDE = os.path.join(ROOT, "docs", "user-guide", "USER_GUIDE.html")
if os.path.isfile(GUIDE):
    datas.append((GUIDE, os.path.join("docs", "user-guide")))
    print("INFO: bundling user guide", GUIDE)
else:
    print("WARNING: user guide not found, building without it:", GUIDE)

# --- hidden imports ---------------------------------------------------------------------------
# keyring picks its backend by name/entry point at run time; PyInstaller cannot see that.
# Windows backend = Windows Credential Manager (WinVaultKeyring), implemented on pywin32-ctypes.
hiddenimports = [
    "keyring.backends.Windows",
    "keyring.backends.fail",
    "keyring.backends.null",
    "keyring.backends.chainer",
    "win32ctypes.core",
    "win32ctypes.pywin32",
    "win32ctypes.pywin32.pywintypes",
    "win32ctypes.pywin32.win32cred",
]
hiddenimports += collect_submodules("keyring.backends")
hiddenimports += collect_submodules("win32ctypes")

# --- excludes (test tooling, packaging tooling and unused heavy modules) ----------------------
excludes = [
    "pytest", "_pytest", "pluggy", "iniconfig", "tests",
    "PyInstaller", "pyinstaller_hooks_contrib", "altgraph", "pefile",
    "pip", "setuptools", "pkg_resources", "wheel",
    "IPython", "jupyter", "matplotlib", "numpy", "pandas", "scipy", "PIL",
]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed executables trigger more antivirus false positives
    console=False,  # windowed: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "app.ico"),
    version=os.path.join(ROOT, "version_info.txt"),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
