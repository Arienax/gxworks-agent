"""Compatible desktop entry point; no business logic lives at the source root."""
if __name__ == "__main__":
    from ui.desktop.main_window import main
    main()
else:
    import sys
    from ui.desktop import main_window as _implementation
    sys.modules[__name__] = _implementation
