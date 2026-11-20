"""Entry point: ``python -m ytdownloader``."""

import sys


def main() -> int:
    # The PySide6 window is introduced in iteration 2 (see docs/PLAN.md).
    print("ytdownloader: GUI not implemented yet, use the legacy ytdownload.py for now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
