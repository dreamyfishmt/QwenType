# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for QwenType: windowed, onefile, aggressively trimmed Qt.
# Build with:  uv run pyinstaller --noconfirm --clean qwentype.spec   (or .\build.ps1 build)

import os
import shutil
import struct
import sys
from importlib.metadata import version as _dist_version
from pathlib import Path

from packaging.version import Version
from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH)
SRC = ROOT / "src"
APP = "QwenType"

# Only QtCore, QtGui and QtWidgets are used. Everything else is excluded.
QT_UNUSED = [
    "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender",
    "QtAxContainer", "QtBluetooth", "QtCharts", "QtConcurrent", "QtDataVisualization", "QtDBus",
    "QtDesigner", "QtGraphs", "QtGraphsWidgets", "QtHelp", "QtHttpServer", "QtLocation",
    "QtMultimedia", "QtMultimediaWidgets", "QtNetwork", "QtNetworkAuth", "QtNfc", "QtOpenGL",
    "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets", "QtPositioning", "QtPrintSupport", "QtQml",
    "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickTest", "QtQuickWidgets", "QtRemoteObjects",
    "QtScxml", "QtSensors", "QtSerialBus", "QtSerialPort", "QtSpatialAudio", "QtSql",
    "QtStateMachine", "QtSvg", "QtSvgWidgets", "QtTest", "QtTextToSpeech", "QtUiTools",
    "QtWebChannel", "QtWebEngineCore", "QtWebEngineQuick", "QtWebEngineWidgets", "QtWebSockets",
    "QtWebView", "QtXml",
]
EXCLUDES = [f"PySide6.{m}" for m in QT_UNUSED] + [
    "tkinter", "_tkinter", "pydoc_data", "lib2to3", "setuptools", "pip", "distutils",
    "numpy.f2py", "win32com", "pythoncom", "win32ui", "pythonwin",
]

# Qt plugins actually needed by a QtWidgets tray app on Windows. No image format
# plugins: icons are drawn in code and PNG/BMP support is built into QtGui.
KEEP_PLUGINS = {
    "platforms/qwindows.dll",
    "styles/qmodernwindowsstyle.dll",  # Qt >= 6.7 default style
    "styles/qwindowsvistastyle.dll",  # older Qt 6
}
# Equivalent plugins when building elsewhere (only for testing the spec).
KEEP_PLUGINS_OTHER = {"platforms/libqxcb.so", "platforms/libqoffscreen.so", "platforms/libqcocoa.dylib"}
# Qt / helper DLLs that only the excluded modules (or OpenGL/RHI) need.
DROP_DLL_PREFIXES = (
    "qt6network", "qt6pdf", "qt6qml", "qt6quick", "qt6svg", "qt6opengl", "qt6virtualkeyboard",
    "qt6multimedia", "qt6webengine", "qt6webchannel", "qt6websockets", "qt6designer", "qt6help",
    "qt6sql", "qt6test", "qt6xml", "qt6printsupport", "qt6concurrent", "qt63d",
    "qt6charts", "qt6datavisualization", "qt6graphs", "qt6bluetooth", "qt6sensors",
    "qt6serialport", "qt6positioning", "qt6location", "qt6shadertools", "qt6labs",
    "opengl32sw", "d3dcompiler_",
)


def _keep(dest: str) -> bool:
    # Windows wheels: PySide6/plugins/..., other platforms: PySide6/Qt/plugins/...
    d = dest.replace("\\", "/").lower().replace("pyside6/qt/", "pyside6/")
    if d.startswith("pyside6/translations/"):
        return False
    if d.startswith("pyside6/plugins/"):
        plugin = d[len("pyside6/plugins/"):]
        return plugin in KEEP_PLUGINS or plugin in KEEP_PLUGINS_OTHER
    name = d.rsplit("/", 1)[-1]
    if name.startswith("lib"):
        name = name[3:]
    if d.startswith("pyside6/") and name.startswith(DROP_DLL_PREFIXES):
        return False
    return True


def _trim(toc, dropped):
    kept = []
    for entry in toc:
        if _keep(entry[0]):
            kept.append(entry)
        else:
            dropped.append(entry)
    return kept


def _make_icon(path: Path):
    """Render the in-code tray icon to a multi-size .ico (PNG entries)."""
    try:
        sys.path.insert(0, str(SRC))
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QGuiApplication

        from qwentype.tray import draw_icon

        _app = QGuiApplication.instance() or QGuiApplication([])
        images = []
        for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            draw_icon(size).save(buf, "PNG")
            images.append((size, bytes(buf.data())))
        header = struct.pack("<HHH", 0, 1, len(images))
        offset = 6 + 16 * len(images)
        entries, blobs = b"", b""
        for size, png in images:
            entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
            offset += len(png)
            blobs += png
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header + entries + blobs)
        return str(path)
    except Exception as e:  # the icon is cosmetic: never fail the build for it
        print(f"[QwenType] icon generation skipped: {e}")
        return None


icon = _make_icon(Path(workpath) / "QwenType.ico")

# Version from the installed package metadata (pyproject.toml; CI sets it from the git tag).
VERSION = _dist_version("qwentype")


def _make_version_file(path: Path) -> str:
    """Windows VERSIONINFO resource, shown in Explorer -> Properties -> Details."""
    nums = (list(Version(VERSION).release) + [0, 0, 0, 0])[:4]
    tup = tuple(nums)
    strings = {
        "CompanyName": APP,
        "FileDescription": APP,
        "FileVersion": VERSION,
        "InternalName": APP,
        "OriginalFilename": f"{APP}.exe",
        "ProductName": APP,
        "ProductVersion": VERSION,
        "LegalCopyright": "Copyright (c) 2026 dreamyfishmt. License: AGPL-3.0-or-later",
    }
    table = ", ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}, mask=0x3f, flags=0x0, OS=0x40004,\n"
        "    fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        f"  kids=[StringFileInfo([StringTable('040904B0', [{table}])]),\n"
        "        VarFileInfo([VarStruct('Translation', [1033, 1200])])]\n"
        ")\n",
        encoding="utf-8",
    )
    return str(path)


version_file = _make_version_file(Path(workpath) / "version_info.txt")

a = Analysis(
    [str(SRC / "qwentype" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=copy_metadata("qwentype"),  # lets qwentype.__version__ work in the exe
    hiddenimports=["win32crypt"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=list(EXCLUDES),  # Analysis mutates the list
    noarchive=False,
    optimize=1,
)

dropped = []
a.binaries = _trim(a.binaries, dropped)
a.datas = _trim(a.datas, dropped)

pyz = PYZ(a.pure)

USE_UPX = shutil.which("upx") is not None
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=USE_UPX,
    # UPX breaks Control Flow Guard DLLs (Qt, MSVC runtime, Python) - leave those alone.
    upx_exclude=[
        "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
        "python3.dll", "python311.dll", "python312.dll", "python313.dll", "python314.dll",
        "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "qwindows.dll", "qmodernwindowsstyle.dll",
        "qwindowsvistastyle.dll", "pyside6.abi3.dll", "shiboken6.abi3.dll",
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    version=version_file,
)

# --- report ---------------------------------------------------------------------
print()
print("=" * 72)
print(f"[QwenType] Excluded Python modules ({len(EXCLUDES)}):")
for i in range(0, len(EXCLUDES), 4):
    print("    " + ", ".join(EXCLUDES[i:i + 4]))
saved = 0
print(f"[QwenType] Dropped Qt plugins / translations / DLLs ({len(dropped)} files):")
for entry in sorted(dropped, key=lambda e: e[0].lower()):
    try:
        size = os.path.getsize(entry[1])
    except OSError:
        size = 0
    saved += size
    print(f"    {entry[0]}  ({size / 1024:.0f} KiB)")
print(f"[QwenType] Dropped total: {saved / 1024 / 1024:.1f} MiB (uncompressed)")
print(f"[QwenType] UPX: {'enabled' if USE_UPX else 'not found, disabled'}")
exe_path = Path(DISTPATH) / (APP + (".exe" if sys.platform == "win32" else ""))
if exe_path.exists():
    print(f"[QwenType] Final executable: {exe_path}  {exe_path.stat().st_size / 1024 / 1024:.1f} MiB  (version {VERSION})")
print("=" * 72)
