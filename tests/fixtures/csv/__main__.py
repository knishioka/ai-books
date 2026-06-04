"""``python -m tests.fixtures.csv [--update] [fixture]`` → the CSV golden CLI.

A dedicated ``__main__`` (rather than running ``golden`` with ``-m``) so the package's
``__init__`` import of :mod:`.golden` does not trip runpy's "already imported" warning.
"""

from __future__ import annotations

from .golden import main

if __name__ == "__main__":
    raise SystemExit(main())
