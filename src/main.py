"""Stable desktop entrypoint for source checkouts and PyInstaller."""
from ui.desktop.application import run

if __name__ == "__main__":
    raise SystemExit(run())
