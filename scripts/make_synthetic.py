"""Generate the synthetic Brightwater Utilities letters.

Same as ``icsel samples``; kept as a script for readers who browse ``scripts/``.

Usage (from the repository root, after ``pip install -e .``)::

    python scripts/make_synthetic.py [--out samples/letters] [--layouts PATH]

All names, addresses and text produced are fictional.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from icsel.synthetic import generate


def main() -> None:
    """Parse arguments and write the letters plus ``manifest.json``."""
    p = argparse.ArgumentParser(description="Generate synthetic sample letters.")
    p.add_argument("--out", type=Path, default=Path("samples") / "letters")
    p.add_argument("--layouts", type=Path, default=None, help="default: bundled layouts")
    args = p.parse_args()
    manifest = generate(args.out, args.layouts)
    print(f"Wrote letters and {manifest}")


if __name__ == "__main__":
    main()
