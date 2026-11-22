import json
import shutil
import socket
from pathlib import Path

import pdfplumber
import pytest

from icsel.cli import EXIT_OK, EXIT_SOME_FAILED, EXIT_USAGE, main
from icsel.profile import load_profile


def test_match_writes_json_and_sample(letters_dir: Path, tmp_path: Path, capsys) -> None:
    rc = main(["match", str(letters_dir / "L04-subject-right-3line.pdf"), "--out", str(tmp_path)])
    assert rc == EXIT_OK
    assert "approximation" in capsys.readouterr().out
    result_path = tmp_path / "L04-subject-right-3line.result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["best_layout"] == "subject-right"
    with pdfplumber.open(tmp_path / "L04-subject-right-3line.sample.pdf") as pdf:
        text = " ".join(pdf.pages[0].extract_text().split())  # the notice may wrap
    assert "Not output from AEM Forms Interactive Communications" in text


def test_match_accepts_a_directory(letters_dir: Path, manifest: list[dict], tmp_path: Path) -> None:
    assert main(["match", str(letters_dir), "--out", str(tmp_path)]) == EXIT_OK
    assert len(list(tmp_path.glob("*.result.json"))) == len(manifest)


def test_match_explicit_layouts_file(letters_dir: Path, layouts_dir: Path, tmp_path: Path) -> None:
    rc = main(
        [
            "match",
            str(letters_dir / "L01-classic.pdf"),
            "--layouts",
            str(layouts_dir / "classic-left.json"),
            "--layouts",
            str(layouts_dir / "tall-address.json"),
            "--out",
            str(tmp_path),
        ]
    )
    assert rc == EXIT_OK
    result = json.loads((tmp_path / "L01-classic.result.json").read_text(encoding="utf-8"))
    assert {r["layout_id"] for r in result["ranking"]} == {"classic-left", "tall-address"}


def test_match_reports_unreadable_pdf_and_continues(
    letters_dir: Path, tmp_path: Path, capsys
) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    shutil.copy(letters_dir / "L01-classic.pdf", folder)
    (folder / "broken.pdf").write_bytes(b"not a pdf")
    rc = main(["match", str(folder), "--out", str(tmp_path / "out")])
    assert rc == EXIT_SOME_FAILED
    assert "broken.pdf" in capsys.readouterr().err
    assert (tmp_path / "out" / "L01-classic.result.json").exists()


def test_missing_input_is_a_usage_error(tmp_path: Path, capsys) -> None:
    assert main(["match", str(tmp_path / "nope.pdf")]) == EXIT_USAGE
    assert "no such file" in capsys.readouterr().err


def test_profile_roundtrip(letters_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "draft.json"
    pdf = str(letters_dir / "L01-classic.pdf")
    assert main(["profile", pdf, "--id", "draft", "-o", str(out)]) == EXIT_OK
    prof = load_profile(out)
    assert prof.id == "draft"
    address = prof.zone("address")
    assert address is not None and address.max_lines == 3


def test_profile_folder_writes_one_file_per_pdf(
    letters_dir: Path, manifest: list[dict], tmp_path: Path
) -> None:
    assert main(["profile", str(letters_dir), "-o", str(tmp_path / "profiles")]) == EXIT_OK
    assert len(list((tmp_path / "profiles").glob("*.json"))) == len(manifest)


def test_samples_command(tmp_path: Path) -> None:
    assert main(["samples", "--out", str(tmp_path)]) == EXIT_OK
    assert (tmp_path / "manifest.json").exists()


def test_network_guard_is_active() -> None:
    with pytest.raises(RuntimeError, match="network access"):
        socket.create_connection(("example.invalid", 80))
