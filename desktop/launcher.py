"""Entry point for frozen builds (PyInstaller cannot run a package's __main__ directly)."""
from sauti_desktop.app import main

if __name__ == "__main__":
    main()
