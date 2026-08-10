# -*- mode: python ; coding: utf-8 -*-
# Build the internal "Thickness License Studio" desktop app (windowed, no console).
#   cd tools && ../local/.build-venv-win/Scripts/pyinstaller --noconfirm license_app.spec
# Output: tools/dist/license-studio.exe
# The Ed25519 signing key stays OUTSIDE the exe (in ~/thickness-license-keys);
# the app reads it at runtime, so it is never embedded in the binary.
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('cryptography')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['license_app.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='license-studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
