"""PyInstaller entry script for EPMTaskDeployer.exe. Runs the same code as `python -m app.main`."""

import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
