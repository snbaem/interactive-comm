"""Command-line interface: ``icsel match``, ``profile``, ``portfolio``, ``serve`` and ``samples``.

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
from .config import ConfigError, load_config
from .dimensions import UNITS, diff_layouts, measure
from .explain import explain_ranking
from .extract import ExtractionError, LetterStructure, extract
from .inputs import InputError, collect_files, unique_stems
from .layoutview import pdf_to_png, render_comparison, render_layout_view
from .llm import Assistant, LLMError, available_backends, get_assistant
from .match import LOW_FIT_THRESHOLD, TIE_MARGIN, MatchResult
from .pipeline import analyze
from .portfolio import (
    DUPLICATE_THRESHOLD,
    STATUS_OK,
    LetterRecord,
    Settings,
    find_duplicate_groups,
    plan_ai,
    run_ai,
    scan,
    write_csv,
    write_markdown,
)
from .profile import LayoutProfile, ProfileError, Zone, default_layouts_dir, load_profiles
from .render import render_sample
from .report import APPROX_NOTE, build_result, dimensions_doc
from .synthetic import generate, generate_portfolio

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
    scoring = load_config(args.config)
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
        analysis = analyze(letter, layouts, assistant, scoring)
        results = analysis.results
        best = results[0]
        sample = render_sample(
            letter,
            by_id[best.layout_id],
            out_dir / f"{stems[pdf]}.sample.pdf",
            show_zones=not args.no_zones,
        )
        best_layout = by_id[best.layout_id]
        result = build_result(
            letter,
            results,
            sample,
            analysis.llm,
            explanation=explain_ranking(letter, layouts, results, scoring, args.units),
            dimensions=dimensions_doc(letter, best_layout),
        )
        if args.dimensions:
            view = render_layout_view(
                best_layout,
                out_dir / f"{stems[pdf]}.dimensions.pdf",
                args.units,
                letter=letter,
            )
            pdf_to_png(view, view.with_suffix(".png"))
        json_path = out_dir / f"{stems[pdf]}.result.json"
        json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        _print_ranking(pdf.name, results)
        print(f"  {result['explanation']}")
        if assistant.enabled:
            _print_ai_result(analysis.llm)
        print(f"  Wrote {json_path} and {sample.name}")
    print(APPROX_NOTE)
    if failed:
        print(f"{failed} of {len(letters)} file(s) could not be read.", file=sys.stderr)
        return EXIT_SOME_FAILED
    return EXIT_OK


def _resolve_layout(layouts: list[LayoutProfile], ref: str) -> LayoutProfile:
    """Find a layout by id, or load it from a profile JSON path."""
    for lay in layouts:
        if lay.id == ref:
            return lay
    path = Path(ref)
    if path.suffix == ".json" and path.is_file():
        return load_profiles(path)[0]
    known = ", ".join(lay.id for lay in layouts)
    raise ProfileError(f"no layout {ref!r}; known layouts: {known} (or give a profile .json path)")


def cmd_show_layout(args: argparse.Namespace) -> int:
    """Draw a layout with its measurements (PDF and/or PNG)."""
    layouts = load_profiles(args.layouts)
    layout = _resolve_layout(layouts, args.layout)
    other = _resolve_layout(layouts, args.compare) if args.compare else None
    letter = extract(args.letter) if args.letter else None
    if args.diff and other is None and letter is None:
        raise ProfileError("--diff needs --compare LAYOUT or --letter PDF to compare against")
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    if other is not None and not args.diff:
        stem = f"compare-{layout.id}-vs-{other.id}"
        pdf = render_comparison(layout, other, out_dir / f"{stem}.pdf", args.units)
    else:
        stem = f"layout-{layout.id}" + (f"-diff-{other.id}" if other else "")
        pdf = render_layout_view(
            layout, out_dir / f"{stem}.pdf", args.units, letter=letter, reference=other
        )
    written = [pdf]
    if args.format in ("png", "both"):
        written.append(pdf_to_png(pdf, pdf.with_suffix(".png")))
    if args.format == "png":
        pdf.unlink()
        written = written[1:]
    for p in written:
        print(f"Wrote {p}")
    if args.json:
        doc = {"layout": measure(layout).to_dict()}
        if other is not None:
            doc["differences_vs_compare"] = [d.to_dict() for d in diff_layouts(layout, other)]
        if letter is not None:
            doc["letter_vs_layout"] = dimensions_doc(letter, layout)["letter_vs_layout"]
        jp = out_dir / f"{stem}.json"
        jp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {jp}")
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
    if args.portfolio:
        out = args.out or Path("samples") / "portfolio"
        manifest = generate_portfolio(out, args.layouts)
        what = "synthetic portfolio files (letters plus a few deliberate problem files)"
    else:
        out = args.out or Path("samples") / "letters"
        manifest = generate(out, args.layouts)
        what = "synthetic letters"
    count = len(json.loads(manifest.read_text(encoding="utf-8")))
    print(f"Wrote {count} {what} and {manifest}")
    return EXIT_OK


def _progress(label: str) -> Any:
    """Return a callback that prints ``label i/n`` every 25 items and at the end."""

    def show(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            print(f"  {label} {done}/{total}", file=sys.stderr)

    return show


def _confirm(question: str) -> bool:
    """Ask a yes/no question; anything but an interactive "y" or "yes" means no."""
    if not sys.stdin.isatty():
        print("Not an interactive terminal, so the AI step needs --yes.", file=sys.stderr)
        return False
    return input(question).strip().lower() in ("y", "yes")


def _portfolio_ai_step(
    args: argparse.Namespace,
    assistant: Assistant,
    records: list[LetterRecord],
    layouts: list[LayoutProfile],
    settings: Settings,
) -> dict[str, Any]:
    """Plan, estimate, confirm and run the AI step; return the summary for the report."""
    _print_ai_notice(assistant)
    plan = plan_ai(records, assistant)
    print(
        f"AI step plan: up to {plan.calls} model call(s) for {len(records)} letter(s): "
        f"{len(plan.classify)} to label unknown blocks, {len(plan.explain)} to explain "
        "ambiguous, weak or no-fit letters. Letters that fit one layout cleanly make no call."
    )
    if plan.calls == 0:
        line = "- Nothing to ask: no unknown blocks and no ambiguous, weak or no-fit letters."
        return {"line": line}
    if not (args.yes or _confirm("Proceed with the AI step? [y/N] ")):
        print("Skipped the AI step; the report is rule-based only.")
        return {"line": "- Skipped (not confirmed); the report is rule-based only."}
    stopped = run_ai(plan, layouts, assistant, settings, _progress("AI"))
    info = assistant.describe()
    touched = len({id(r) for r in [*plan.classify, *plan.explain]})
    line = f"- Ran {info['backend']} / {info['model']} on {touched} letter(s)."
    return {"line": line, "stopped": stopped}


def cmd_portfolio(args: argparse.Namespace) -> int:
    """Match every letter in the given folders and write ``portfolio.csv`` and ``portfolio.md``."""
    layouts = load_profiles(args.layouts)
    paths = collect_files(args.letters, ".pdf", recursive=args.recursive)
    scoring = load_config(args.config)
    settings = Settings(
        scoring.low_fit_threshold if args.min_score is None else args.min_score,
        scoring.tie_margin if args.ambiguity_margin is None else args.ambiguity_margin,
        args.duplicate_threshold,
        scoring,
    )
    assistant = get_assistant(args.llm)
    print(f"Matching {len(paths)} PDF(s) against {len(layouts)} layout(s)...", file=sys.stderr)
    records = scan(paths, layouts, settings, _progress("matched"))
    groups = find_duplicate_groups(records, settings.duplicate_threshold)

    ai_summary: dict[str, Any] | None = None
    if assistant.enabled:
        ai_summary = _portfolio_ai_step(args, assistant, records, layouts, settings)

    out: Path = args.out
    csv_path = write_csv(records, out / "portfolio.csv")
    md_path = write_markdown(records, groups, layouts, settings, out / "portfolio.md", ai_summary)
    matched = sum(1 for r in records if r.status == STATUS_OK)
    unreadable = len(records) - matched
    print(f"Scanned {len(records)} PDF(s): {matched} matched, {unreadable} unreadable or no text.")
    print(f"Wrote {csv_path} and {md_path}")
    print(APPROX_NOTE)
    if unreadable:
        print(f"{unreadable} of {len(records)} file(s) could not be matched.", file=sys.stderr)
        return EXIT_SOME_FAILED
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

    config_help = (
        "scoring settings JSON (weights, position scale, width tolerance, thresholds); "
        "see src/icsel/data/scoring.json for the defaults"
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
    m.add_argument(
        "--dimensions",
        action="store_true",
        help="also write <name>.dimensions.pdf/.png: the best layout with measurements and the "
        "letter's blocks overlaid",
    )
    m.add_argument(
        "--units", choices=UNITS, default="in", help="units for measurements (default: in)"
    )
    m.add_argument("--config", type=Path, metavar="FILE", help=config_help)
    m.add_argument("--llm", metavar="BACKEND", help=llm_help)
    m.set_defaults(func=cmd_match)

    sl = sub.add_parser(
        "show-layout",
        help="draw a layout with distances, margins, gaps and sizes (PDF/PNG)",
    )
    sl.add_argument("layout", metavar="LAYOUT", help="layout id, or path to a profile .json")
    sl.add_argument("--layouts", action="append", type=Path, metavar="PATH", help=layouts_help)
    sl.add_argument("--units", choices=UNITS, default="in", help="in, cm or pt (default: in)")
    sl.add_argument("--out", type=Path, default=Path("out") / "layouts", help="output folder")
    sl.add_argument(
        "--format", choices=("pdf", "png", "both"), default="both", help="default: both"
    )
    sl.add_argument(
        "--compare",
        metavar="LAYOUT",
        help="a second layout: side by side, or with --diff overlaid on the first",
    )
    sl.add_argument(
        "--diff",
        action="store_true",
        help="overlay the --compare layout (or --letter) and list differences such as "
        '"subject 0.3" lower than layout"',
    )
    sl.add_argument("--letter", type=Path, metavar="PDF", help="overlay a letter's blocks")
    sl.add_argument("--json", action="store_true", help="also write the measurements as JSON")
    sl.set_defaults(func=cmd_show_layout)

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

    po = sub.add_parser(
        "portfolio", help="match a folder of letters and summarize layout fit, duplicates and gaps"
    )
    po.add_argument(
        "letters",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="letter PDF(s), folder(s) or glob pattern(s)",
    )
    po.add_argument("--layouts", action="append", type=Path, metavar="PATH", help=layouts_help)
    po.add_argument(
        "--out",
        type=Path,
        default=Path("out") / "portfolio",
        help="output folder for portfolio.csv and portfolio.md (default: out/portfolio)",
    )
    po.add_argument("--config", type=Path, metavar="FILE", help=config_help)
    po.add_argument("-r", "--recursive", action="store_true", help="also search subfolders")
    po.add_argument(
        "--min-score",
        type=float,
        default=None,
        help=f"best score below this is a weak fit (default: {LOW_FIT_THRESHOLD}, or --config)",
    )
    po.add_argument(
        "--ambiguity-margin",
        type=float,
        default=None,
        help="fitting layouts within this score of the best are a near-tie "
        f"(default: {TIE_MARGIN}, or --config)",
    )
    po.add_argument(
        "--duplicate-threshold",
        type=float,
        default=DUPLICATE_THRESHOLD,
        help="body-text similarity at or above which letters are near-duplicates "
        f"(default: {DUPLICATE_THRESHOLD})",
    )
    po.add_argument("--llm", metavar="BACKEND", help=llm_help)
    po.add_argument(
        "--yes",
        action="store_true",
        help="run the AI step without asking after the call estimate (needed when not interactive)",
    )
    po.set_defaults(func=cmd_portfolio)

    s = sub.add_parser("samples", help="generate synthetic sample letters")
    s.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output folder (default: samples/letters, or samples/portfolio with --portfolio)",
    )
    s.add_argument(
        "--portfolio",
        action="store_true",
        help="generate the larger demo folder for `icsel portfolio` instead: ~30 letters with "
        "varied bodies plus deliberate problems (a duplicate, a no-fit letter, a scan, a bad file)",
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
    except (ExtractionError, ProfileError, ConfigError, InputError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
