"""Scoring config and layout-profile validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from icsel.config import (
    ConfigError,
    ScoringConfig,
    config_from_dict,
    default_config_path,
    load_config,
)
from icsel.extract import Block, LetterStructure
from icsel.match import ROLE_WEIGHTS, score_layout
from icsel.profile import (
    ProfileError,
    default_layouts_dir,
    load_profile,
    profile_from_dict,
    schema_path,
    validate_profile_dict,
)

GOOD = {
    "id": "t",
    "page": {"width": 612, "height": 792},
    "zones": [{"role": "address", "x": 72, "y": 144, "width": 250, "height": 80, "max_lines": 6}],
}


def test_bundled_defaults_file_equals_code_defaults() -> None:
    assert load_config(default_config_path()) == ScoringConfig()
    assert ScoringConfig().role_weights == ROLE_WEIGHTS


def test_partial_config_keeps_other_defaults() -> None:
    cfg = config_from_dict({"role_weights": {"subject": 5}, "tie_margin": 0.1})
    assert cfg.role_weights["subject"] == 5.0
    assert cfg.role_weights["address"] == 3.0
    assert cfg.tie_margin == 0.1
    assert cfg.position_scale == 72.0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"bogus": 1}, "unknown setting 'bogus'"),
        ({"closeness_share": 2}, "'closeness_share' must be a share"),
        ({"position_scale": "far"}, "must be a number"),
        ({"role_weights": {"footer": 1}}, "not scored"),
        ({"role_weights": {"nope": 1}}, "unknown role"),
        ({"role_weights": {"address": -1}}, "must not be negative"),
        (
            {"role_weights": {"address": 0, "subject": 0, "date": 0, "body": 0, "reference": 0}},
            "all be zero",
        ),
    ],
)
def test_bad_config_is_rejected_with_a_clear_message(data: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        config_from_dict(data, source="cfg.json")


def test_config_file_errors(tmp_path: Path) -> None:
    bad = tmp_path / "c.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(bad)
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.json")


def test_weights_change_the_score() -> None:
    letter = LetterStructure(
        source="x.pdf",
        page_width=612,
        page_height=792,
        page_count=1,
        blocks=[Block(x=80, y=260, width=200, height=12, lines=["Hi"], role="subject")],
    )
    from icsel.profile import profile_from_dict as mk

    layout = mk(
        {
            "id": "a",
            "page": {"width": 612, "height": 792},
            "zones": [{"role": "subject", "x": 72, "y": 252, "width": 468, "height": 30}],
        }
    )
    base = score_layout(letter, layout)
    stricter = score_layout(letter, layout, config_from_dict({"position_scale": 10}))
    assert stricter.score < base.score


def test_profile_errors_list_every_problem() -> None:
    bad = copy.deepcopy(GOOD)
    bad["zones"][0]["width"] = -5
    bad["zones"][0]["role"] = "adress"
    bad["zones"][0]["max_lines"] = 0
    bad["zones"].append({"role": "body", "x": 600, "y": 0, "width": 100, "height": 10})
    with pytest.raises(ProfileError) as info:
        profile_from_dict(bad, source="bad.json")
    text = str(info.value)
    assert "bad.json" in text
    assert "zones[0].width: must be greater than 0" in text
    assert "zones[0].role: 'adress' is not one of" in text
    assert "zones[0].max_lines" in text
    assert "zones[1]: right edge 700 is beyond the page width 612" in text


def test_profile_missing_keys_and_bad_json(tmp_path: Path) -> None:
    assert "id: required but missing" in validate_profile_dict({"page": {}, "zones": []})
    p = tmp_path / "p.json"
    p.write_text("[1,", encoding="utf-8")
    with pytest.raises(ProfileError, match="not valid JSON"):
        load_profile(p)


def test_schema_and_validator_agree_on_bundled_and_broken_profiles() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    for f in sorted(default_layouts_dir().glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert not list(validator.iter_errors(data)), f
        assert validate_profile_dict(data) == [], f

    def mutate(path: list, value: object) -> dict:
        d = copy.deepcopy(GOOD)
        cur = d
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value
        return d

    broken = [
        mutate(["zones", 0, "role"], "nope"),
        mutate(["zones", 0, "width"], 0),
        mutate(["zones", 0, "x"], -1),
        mutate(["zones", 0, "max_lines"], 0),
        mutate(["zones", 0, "required"], "yes"),
        mutate(["zones", 0, "extra"], 1),
        mutate(["page", "width"], -1),
        mutate(["zones"], []),
        mutate(["id"], ""),
        mutate(["profile_version"], 2),
    ]
    for data in broken:
        assert list(validator.iter_errors(data)), data
        assert validate_profile_dict(data), data
