# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_dir = Path(SPECPATH)
spellchecker_data = collect_data_files("spellchecker")
mind_submodules = collect_submodules("mind")


a = Analysis(
    [str(project_dir / "mind_app.pyw")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[
        (str(project_dir / "SwiftSlate.pyw"), "."),
        (str(project_dir / "commands.json"), "."),
        (str(project_dir / "assets" / "mind-logo-final.png"), "assets"),
        (str(project_dir / "mind" / "install_update.ps1"), "mind"),
        (str(project_dir / "mind" / "windows_ocr.ps1"), "mind"),
    ] + spellchecker_data,
    hiddenimports=["spellchecker"] + mind_submodules,
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
    name="Mind",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Avoid executable compression, which can increase antivirus scrutiny of
    # the one-file runtime during update extraction.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_dir / "artifacts" / "Mind.ico"),
)
