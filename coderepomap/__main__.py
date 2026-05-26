"""
Entry point for running as module: python -m coderepomap
"""

import sys
from .core.cli import main

if __name__ == "__main__":
    sys.exit(main())
