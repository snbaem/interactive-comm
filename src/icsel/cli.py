"""Command-line interface: ``icsel match`` and ``icsel profile``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extract import ExtractionError, LetterStructure, extract
from .match import rank
from .profile import LayoutProfile, ProfileError, Zone, load_profiles
from .render import render_sample

APPROX_NOTE = (
    "Note: the sample PDF is a local approximation for checking fit. "
    "It is not AEM Forms Interactive Communications output."
)


def structure_to_profile(letter: LetterStructure, profile_id: str, name: str = "") -> LayoutProfile:
    """Turn a letter's detected blocks into a draft layout profile.

    Each page-1 role becomes one zone sized to the detected text, with
    ``max_lines`` set to the detected line count. Review and widen the zones
    by hand before using the result as a real layout.
    """
    zones = []
    for role in ("letterhead", "date", "address", "subject", "body", "footer"):
        region = letter.region(role)
        if region is None:
            continue
        max_lines = region.line_count if role in ("date", "address", "subject") else None
        zones.append(Zone(role, round(region.x, 1), round(region.y, 1), round(region.width, 1),
                          round(region.height, 1), max_lines, required=(role == "address")))
    return LayoutProfile(profile_id, name or profile_id, letter.page_width, letter.page_height,
                         zones, description=f"Draft profile derived from {Path(letter.source).name}")


def cmd_match(args: argparse.Namespace) -> int:
    """Rank layouts for one letter and write the result JSON and sample PDF."""
    layouts = load_profiles(args.layouts)
    letter = extract(args.letter)
    results = rank(letter, layouts)
    best = results[0]
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.letter).stem
    sample = render_sample(letter, next(lay for lay in layouts if lay.id == best.layout_id),
                           out_dir / f"{stem}.sample.pdf")
    result = {
        "letter": letter.to_dict(),
        "best_layout": best.layout_id,
        "best_fits": best.fits,
        "ranking": [r.to_dict() for r in results],
        "sample_pdf": str(sample),
        "fidelity": APPROX_NOTE,
    }
    json_path = out_dir / f"{stem}.result.json"
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"{Path(args.letter).name}")
    for r in results:
        flag = "fits" if r.fits else "DOES NOT FIT"
        print(f"  {r.layout_id:20} {r.score:6.3f}  {flag}")
        for f in r.failures:
            print(f"      - {f}")
    if not best.fits:
        print("  No layout passes the hard checks; showing the highest-scoring one.")
    print(f"Best layout: {best.layout_id}")
    print(f"Wrote {json_path}")
    print(f"Wrote {sample}")
    print(APPROX_NOTE)
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    """Print or write a draft layout profile derived from a PDF."""
    letter = extract(args.pdf)
    profile = structure_to_profile(letter, args.id or Path(args.pdf).stem)
    text = json.dumps(profile.to_dict(), indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    p = argparse.ArgumentParser(prog="icsel", description="Pick the best-fitting layout for a letter PDF.")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("match", help="rank layouts for a letter and render a sample PDF")
    m.add_argument("letter", type=Path, help="text-based letter PDF")
    m.add_argument("--layouts", type=Path, default=Path("layouts"), help="folder of layout profiles")
    m.add_argument("--out", type=Path, default=Path("out"), help="output folder")
    m.set_defaults(func=cmd_match)

    pr = sub.add_parser("profile", help="derive a draft layout profile from a PDF")
    pr.add_argument("pdf", type=Path)
    pr.add_argument("--id", help="profile id (default: file stem)")
    pr.add_argument("-o", "--output", type=Path, help="write to file instead of stdout")
    pr.set_defaults(func=cmd_profile)
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ExtractionError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
