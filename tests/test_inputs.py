from pathlib import Path

import pytest

from icsel.inputs import InputError, collect_files, unique_stems
from icsel.profile import ProfileError, load_profiles


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_directory_is_case_insensitive_and_sorted(tmp_path: Path) -> None:
    _touch(tmp_path / "b.pdf")
    _touch(tmp_path / "A.PDF")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "sub" / "c.pdf")
    assert [p.name for p in collect_files([tmp_path], ".pdf")] == ["A.PDF", "b.pdf"]
    names = [p.name for p in collect_files([tmp_path], ".pdf", recursive=True)]
    assert sorted(names) == ["A.PDF", "b.pdf", "c.pdf"]


def test_glob_pattern_is_expanded_without_a_shell(tmp_path: Path) -> None:
    _touch(tmp_path / "x1.pdf")
    _touch(tmp_path / "x2.pdf")
    _touch(tmp_path / "y.pdf")
    found = collect_files([str(tmp_path / "x*.pdf")], ".pdf")
    assert [p.name for p in found] == ["x1.pdf", "x2.pdf"]


def test_duplicates_are_removed(tmp_path: Path) -> None:
    f = _touch(tmp_path / "a.pdf")
    assert collect_files([f, tmp_path, str(tmp_path / "*.pdf")], ".pdf") == [f]


def test_missing_path_and_empty_folder(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no such file"):
        collect_files([tmp_path / "missing.pdf"], ".pdf")
    with pytest.raises(InputError, match=r"no .pdf files"):
        collect_files([tmp_path], ".pdf")
    with pytest.raises(InputError, match="pattern matched"):
        collect_files([str(tmp_path / "*.pdf")], ".pdf")


def test_unique_stems_disambiguates_same_name(tmp_path: Path) -> None:
    a = _touch(tmp_path / "north" / "letter.pdf")
    b = _touch(tmp_path / "south" / "letter.pdf")
    c = _touch(tmp_path / "other.pdf")
    assert unique_stems([a, b, c]) == {a: "north__letter", b: "south__letter", c: "other"}


def test_duplicate_layout_ids_are_rejected(layouts_dir: Path, tmp_path: Path) -> None:
    src = layouts_dir / "classic-left.json"
    copy = tmp_path / "copy.json"
    copy.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ProfileError, match="duplicate layout id"):
        load_profiles([src, copy])


def test_bundled_layouts_load(layouts_dir: Path) -> None:
    ids = {p.id for p in load_profiles()}
    assert {"classic-left", "subject-right", "tall-address"} <= ids
    assert layouts_dir.is_dir()
