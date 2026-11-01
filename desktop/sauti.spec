# PyInstaller build for the Sauti tray / menu-bar app.
#   Windows:  pyinstaller desktop/sauti.spec   -> dist/Sauti/Sauti.exe
#   macOS:    pyinstaller desktop/sauti.spec   -> dist/Sauti.app
import sys
from pathlib import Path

HERE = Path(SPECPATH)
hidden = ["keyring.backends.Windows", "keyring.backends.macOS"]
if sys.platform == "win32":
    hidden += ["pynput.keyboard._win32", "pynput.mouse._win32"]
elif sys.platform == "darwin":
    hidden += ["pynput.keyboard._darwin", "pynput.mouse._darwin"]

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(HERE)],
    hiddenimports=hidden,
    excludes=["tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Sauti",
    console=False,
    icon=str(HERE / "assets" / ("sauti.icns" if sys.platform == "darwin" else "sauti.ico")),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Sauti")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Sauti.app",
        icon=str(HERE / "assets" / "sauti.icns"),
        bundle_identifier="org.sauti.desktop",
        info_plist={
            "CFBundleShortVersionString": "0.2.0",
            "LSUIElement": True,  # menu-bar app: no Dock icon
            "NSMicrophoneUsageDescription": "Sauti records your voice only while you hold the dictation key, "
                                            "and sends it to your organisation's own server.",
            "NSAppleEventsUsageDescription": "Sauti pastes dictated text into the app you are using.",
        },
    )
