import json
from pathlib import Path

import pdfplumber

from icsel.cli import main
from icsel.profile import load_profile
from tests.conftest import LAYOUTS_DIR


def test_match_writes_json_and_sample(letters_dir: Path, tmp_path: Path, capsys) -> None:
    rc = main(["match", str(letters_dir / "L04-subject-right-3line.pdf"),
               "--layouts", str(LAYOUTS_DIR), "--out", str(tmp_path)])
    assert rc == 0
    assert "approximation" in capsys.readouterr().out
    result = json.loads((tmp_path / "L04-subject-right-3line.result.json").read_text(encoding="utf-8"))
    assert result["best_layout"] == "subject-right"
    with pdfplumber.open(tmp_path / "L04-subject-right-3line.sample.pdf") as pdf:
        text = pdf.pages[0].extract_text()
    assert "Not output from AEM Forms Interactive Communications" in text


def test_profile_roundtrip(letters_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "draft.json"
    assert main(["profile", str(letters_dir / "L01-classic.pdf"), "--id", "draft", "-o", str(out)]) == 0
    prof = load_profile(out)
    assert prof.id == "draft"
    assert prof.zone("address") is not None and prof.zone("address").max_lines == 3
