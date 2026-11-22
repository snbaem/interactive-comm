"""Command-line interface: ``icsel match``, ``icsel profile`` and ``icsel samples``.

Every path argument accepts files, folders and glob patterns (expanded by
icsel itself, so ``letters/*.pdf`` works in PowerShell and cmd.exe too).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .extract import ExtractionError, LetterStructure, extract
from .inputs import InputError, collect_files, unique_stems
from .llm import Assistant, LLMError, available_backends, get_assistant
from .match import MatchResult
from .pipeline import analyze
from .profile import LayoutProfile, ProfileError, Zone, default_layouts_dir, load_profiles
from .render import render_sample
from .report import APPROX_NOTE, build_result
from .synthetic import generate

EXIT_OK = 0
EXIT_SOME_FAILED = 1
EXIT_USAGE = 2


def structure_to_profile(letter: LetterStructure, profile_id: str, name: str = "") -> LayoutProfile:
    """Turn a letter's detected blocks into a draft layout profile.

    Each page-1 role becomes one zone sized to the detected text, with
    ``max_lines`` set to the detected line count. Review and widen the zones by
    hand before using the result as a real layout.

    Args:
        letter: Structure returned by :func:`icsel.extract.extract`.
        profile_id: ``id`` for the new profile.
        name: Human-readable name; defaults to ``profile_id``.
    """
    zones = []
    for role in ("letterhead", "date", "address", "subject", "body", "footer"):
        region = letter.region(role)
        if region is None:
            continue
        max_lines = region.line_count if role in ("date", "address", "subject") else None
        zones.append(
            Zone(
                role,
                round(region.x, 1),
                round(region.y, 1),
                round(region.width, 1),
                round(region.height, 1),
                max_lines,
                required=(role == "address"),
            )
        )
    return LayoutProfile(
        profile_id,
        name or profile_id,
        letter.page_width,
        letter.page_height,
        zones,
        description=f"Draft profile derived from {Path(letter.source).name}",
    )


def _print_ranking(name: str, results: list[MatchResult]) -> None:
    print(name)
    for r in results:
        flag = "fits" if r.fits else "DOES NOT FIT"
        print(f"  {r.layout_id:20} {r.score:6.3f}  {flag}")
        for f in r.failures:
            print(f"      - {f}")
    if not results[0].fits:
        print("  No layout passes the hard checks; showing the highest-scoring one.")
    print(f"  Best layout: {results[0].layout_id}")


def _print_ai_notice(assistant: Assistant) -> None:
    info = assistant.describe()
    print(
        f"AI step ON: {info['backend']} / {info['model']} (tasks: {', '.join(info['tasks'])}).\n"
        "  Sent: the text of blocks the rules could not label, plus layout facts and scores.\n"
        "  Only use letters you are allowed to send to this service."
    )


def _print_ai_result(llm: dict[str, Any]) -> None:
    for change in llm.get("role_changes", []):
        print(
            f"  AI: block {change['index']} {change['from']} -> {change['to']} "
            f"({change['confidence']:.2f}): {change['reason']}"
        )
    if llm.get("explanation"):
        lines = textwrap.wrap(llm["explanation"], width=88)
        print("  Why (AI): " + "\n            ".join(lines))
    for err in llm.get("errors", []):
        print(f"  AI step warning: {err}", file=sys.stderr)


def cmd_match(args: argparse.Namespace) -> int:
    """Rank layouts for each letter; write a result JSON and sample PDF per letter."""
    layouts = load_profiles(args.layouts)
    by_id = {lay.id: lay for lay in layouts}
    letters = collect_files(args.letters, ".pdf", recursive=args.recursive)
    assistant = get_assistant(args.llm)
    if assistant.enabled:
        _print_ai_notice(assistant)
    stems = unique_stems(letters)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for pdf in letters:
        try:
            letter = extract(pdf)
        except ExtractionError as exc:
            print(f"{pdf.name}\n  skipped: {exc}", file=sys.stderr)
            failed += 1
            continue
        analysis = analyze(letter, layouts, assistant)
        results = analysis.results
        best = results[0]
        sample = render_sample(
            letter,
            by_id[best.layout_id],
            out_dir / f"{stems[pdf]}.sample.pdf",
            show_zones=not args.no_zones,
        )
        result = build_result(letter, results, sample, analysis.llm)
        json_path = out_dir / f"{stems[pdf]}.result.json"
        json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        _print_ranking(pdf.name, results)
        if assistant.enabled:
            _print_ai_result(analysis.llm)
        print(f"  Wrote {json_path} and {sample.name}")
    print(APPROX_NOTE)
    if failed:
        print(f"{failed} of {len(letters)} file(s) could not be read.", file=sys.stderr)
        return EXIT_SOME_FAILED
    return EXIT_OK


def cmd_profile(args: argparse.Namespace) -> int:
    """Derive draft layout profiles from PDFs; print one or write files."""
    pdfs = collect_files(args.pdfs, ".pdf", recursive=args.recursive)
    if len(pdfs) > 1 and args.id:
        raise InputError("--id can only be used with a single PDF")
    output: Path | None = args.output
    single_to_stdout = len(pdfs) == 1 and output is None
    single_to_file = len(pdfs) == 1 and output is not None and not output.is_dir()
    out_dir: Path | None = None
    if single_to_file:
        assert output is not None
        output.parent.mkdir(parents=True, exist_ok=True)
    elif not single_to_stdout:
        out_dir = output or Path("profiles")
        out_dir.mkdir(parents=True, exist_ok=True)
    stems = unique_stems(pdfs)
    failed = 0
    for pdf in pdfs:
        try:
            letter = extract(pdf)
        except ExtractionError as exc:
            print(f"skipped: {exc}", file=sys.stderr)
            failed += 1
            continue
        profile = structure_to_profile(letter, args.id or stems[pdf])
        text = json.dumps(profile.to_dict(), indent=2) + "\n"
        if single_to_stdout:
            sys.stdout.write(text)
            continue
        target = out_dir / f"{stems[pdf]}.json" if out_dir else output
        assert target is not None
        target.write_text(text, encoding="utf-8")
        print(f"Wrote {target}")
    return EXIT_SOME_FAILED if failed else EXIT_OK


def cmd_samples(args: argparse.Namespace) -> int:
    """Generate the synthetic Brightwater Utilities letters."""
    manifest = generate(args.out, args.layouts)
    count = len(json.loads(manifest.read_text(encoding="utf-8")))
    print(f"Wrote {count} synthetic letters and {manifest}")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the local browser UI."""
    from .web import serve  # imported lazily: only this command needs the web module

    layouts = load_profiles(args.layouts) if args.layouts else None
    assistant = get_assistant(args.llm)
    if assistant.enabled:
        _print_ai_notice(assistant)
    try:
        serve(
            args.host,
            args.port,
            layouts,
            args.workdir,
            open_browser=args.open,
            assistant=assistant,
        )
    except OSError as exc:
        where = f"{args.host}:{args.port}" if args.port is not None else args.host
        print(f"error: cannot listen on {where} ({exc})", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    p = argparse.ArgumentParser(
        prog="icsel",
        description="Pick the best-fitting layout for a text-based letter PDF. "
        "Local prototype; not an official Adobe project.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    layouts_help = (
        "layout profile file, folder or glob; repeatable "
        f"(default: bundled examples in {default_layouts_dir().name}/)"
    )

    llm_help = (
        "optional AI step: " + " | ".join(available_backends()) + " (default: the ICSEL_LLM "
        "environment variable, else none). Sends the text of blocks the rules could not label "
        "to that service."
    )

    m = sub.add_parser("match", help="rank layouts for letters and render sample PDFs")
    m.add_argument(
        "letters",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="letter PDF(s), folder(s) or glob pattern(s)",
    )
    m.add_argument("--layouts", action="append", type=Path, metavar="PATH", help=layouts_help)
    m.add_argument("--out", type=Path, default=Path("out"), help="output folder (default: out)")
    m.add_argument("-r", "--recursive", action="store_true", help="also search subfolders")
    m.add_argument(
        "--no-zones", action="store_true", help="do not draw zone outlines in the sample PDF"
    )
    m.add_argument("--llm", metavar="BACKEND", help=llm_help)
    m.set_defaults(func=cmd_match)

    pr = sub.add_parser("profile", help="derive draft layout profile(s) from PDF(s)")
    pr.add_argument(
        "pdfs", nargs="+", type=Path, metavar="PATH", help="PDF(s), folder(s) or glob pattern(s)"
    )
    pr.add_argument("--id", help="profile id (single PDF only; default: file stem)")
    pr.add_argument(
        "-o",
        "--output",
        type=Path,
        help="single PDF: output file (default: stdout); "
        "several PDFs: output folder (default: profiles)",
    )
    pr.add_argument("-r", "--recursive", action="store_true", help="also search subfolders")
    pr.set_defaults(func=cmd_profile)

    s = sub.add_parser("samples", help="generate synthetic sample letters")
    s.add_argument(
        "--out",
        type=Path,
        default=Path("samples") / "letters",
        help="output folder (default: samples/letters)",
    )
    s.add_argument("--layouts", type=Path, help="layout folder (default: bundled examples)")
    s.set_defaults(func=cmd_samples)

    w = sub.add_parser("serve", help="open a local browser UI to try letters and layouts")
    w.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    w.add_argument(
        "--port",
        type=int,
        default=None,
        help="port (default: 8765, or the next free one; 0 = any free port)",
    )
    w.add_argument("--layouts", action="append", type=Path, metavar="PATH", help=layouts_help)
    w.add_argument(
        "--workdir",
        type=Path,
        default=Path("out") / "web",
        help="folder for uploads and results (default: out/web)",
    )
    w.add_argument("--open", action="store_true", help="open the UI in your default browser")
    w.add_argument("--llm", metavar="BACKEND", help=llm_help)
    w.set_defaults(func=cmd_serve)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    for stream in (sys.stdout, sys.stderr):
        # Never crash on a console that cannot encode a character in a file name.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    # pdfminer logs harmless font warnings (e.g. "Could not get FontBBox") for many PDFs.
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ExtractionError, ProfileError, InputError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
