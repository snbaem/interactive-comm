"""Generate the synthetic Brightwater Utilities letters into samples/letters/.

Usage (from the repository root):

    python scripts/make_synthetic.py [--layouts layouts] [--out samples/letters]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from icsel.synthetic import generate  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--layouts", type=Path, default=Path("layouts"))
    p.add_argument("--out", type=Path, default=Path("samples/letters"))
    args = p.parse_args()
    manifest = generate(args.layouts, args.out)
    print(f"Wrote letters and {manifest}")


if __name__ == "__main__":
    main()
