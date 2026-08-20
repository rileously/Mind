# -*- mode: python ; coding: utf-8 -*-

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32 import versioninfo


project_dir = Path(SPECPATH)
spellchecker_data = collect_data_files("spellchecker")
mind_submodules = collect_submodules("mind")


def version_resource():
    """What Windows shows when it is asked who made this.

    An executable with no version resource has no publisher, no product name
    and no version anywhere in it, which is what the properties dialog and the
    SmartScreen "unrecognised app" panel both read from. Blank there is not
    neutral - it is the shape of software nobody has put their name to, and it
    is the only thing a user has to go on when deciding whether to run it.

    This does not make SmartScreen quieter; only a signing certificate does
    that. It decides what is written in the box once SmartScreen has spoken.

    Read out of mind\\__init__.py rather than repeated here, because a version
    that disagrees with the one the application reports about itself is worse
    than not having one.
    """
    text = (project_dir / "mind" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not found:
        raise SystemExit("Mind.spec: no __version__ found in mind\\__init__.py")
    dotted = found.group(1)
    # Windows wants exactly four numbers; the project keeps three.
    numbers = tuple(int(part) for part in dotted.split("."))
    numbers = (numbers + (0, 0, 0, 0))[:4]
    strings = [
        ("CompanyName", "Musheer Alam"),
        ("FileDescription", "Mind - AI writing workspace"),
        ("FileVersion", dotted),
        ("InternalName", "Mind"),
        ("LegalCopyright", "Copyright (c) 2026 Musheer Alam. MIT Licence."),
        ("OriginalFilename", "Mind.exe"),
        ("ProductName", "Mind"),
        ("ProductVersion", dotted),
    ]
    return versioninfo.VSVersionInfo(
        ffi=versioninfo.FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            versioninfo.StringFileInfo(
                [
                    versioninfo.StringTable(
                        # US English, Unicode - the codes the field names above
                        # are written in, not a choice about who may run this.
                        "040904B0",
                        [versioninfo.StringStruct(k, v) for k, v in strings],
                    )
                ]
            ),
            versioninfo.VarFileInfo([versioninfo.VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

# The Windows 11 context menu handler and its sparse package, built separately by
# shell\build_shell_menu.ps1 because they need MSVC and the Windows SDK. Bundled
# when present rather than required, so Mind still builds without that toolchain;
# the registry verb under "Show more options" is the fallback in that case.
shell_menu_dir = project_dir / "artifacts" / "shell"
shell_menu_data = [
    (str(shell_menu_dir / name), "shell_menu")
    for name in ("MindShellMenu.dll", "MindShellMenu.msix")
    if (shell_menu_dir / name).is_file()
]


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
        (str(project_dir / "mind" / "windows_print.ps1"), "mind"),
        (str(project_dir / "mind" / "windows_toast.ps1"), "mind"),
        (str(project_dir / "mind" / "windows_hotspot.ps1"), "mind"),
    ] + spellchecker_data + shell_menu_data,
    hiddenimports=["spellchecker", "segno"] + mind_submodules,
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
    # Extract the one-file runtime to a stable per-user folder instead of the
    # volatile system temp directory. Storage Sense, Disk Cleanup, and antivirus
    # products delete %TEMP%\_MEIxxxxxx while Mind is still running, which makes
    # the next launch fail with "Failed to load Python DLL ... python312.dll".
    runtime_tmpdir="%LOCALAPPDATA%\\Mind\\Runtime",
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_dir / "artifacts" / "Mind.ico"),
    version=version_resource(),
)
