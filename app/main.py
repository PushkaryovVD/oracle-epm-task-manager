"""Entry point: `python -m app.main` from the project root (or EPMTaskDeployer.exe).

`--self-check` runs non-GUI checks, writes logs/self_check.txt and exits 0 (ok) or 1 (failed).
"""

import logging
import sys


def _set_window_icon(window) -> None:
    try:
        from app.utils.paths import app_icon_path

        icon = app_icon_path()
        if icon.exists():
            window.iconbitmap(str(icon))
    except Exception:  # cosmetic only; never block the app on the icon
        logging.getLogger("app").debug("Window icon not set", exc_info=True)


def main() -> int:
    if "--self-check" in sys.argv[1:]:
        from app.self_check import run_self_check

        return run_self_check()

    from app.utils.logging_setup import configure_logging

    configure_logging()
    log = logging.getLogger("app")
    log.info("Application started")
    try:
        from app.ui.main_window import MainWindow

        window = MainWindow()
        _set_window_icon(window)
        window.mainloop()
    except Exception:
        log.exception("Unhandled error")
        raise
    log.info("Application closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
