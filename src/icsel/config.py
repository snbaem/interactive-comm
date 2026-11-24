"""Scoring settings: weights, position scale and hard-check rules, with defaults.

The defaults live in ``data/scoring.json`` and are the values the matcher always
used. A user file passed with ``--config`` may set any subset of the keys; the rest
keep their defaults. Unknown keys and out-of-range values are rejected with a
message that names the file and the key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .profile import ROLES

#: Roles that can be given a weight (the ones the matcher scores).
SCORED_ROLES = ("address", "subject", "date", "body")


class ConfigError(ValueError):
    """A scoring config file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class ScoringConfig:
    """Every tunable number in the matcher.

    Attributes:
        role_weights: Weight of each scored role in the total (relative, not normalized).
        position_scale: Distance in points at which closeness reaches zero.
        closeness_share: Share of a role's score from closeness; the rest is overlap.
        width_tolerance: Points a block may exceed its zone's width before it fails.
        low_fit_threshold: A best score below this is called a weak fit.
        tie_margin: Fitting layouts within this score of the best count as a near-tie.
        cluster_distance: Layout proposals: two letters belong together when none of their
            address, subject and date boxes starts farther apart than this (points). The
            default, 36 pt (half an inch), is half of ``position_scale``.
        cluster_min_letters: Layout proposals: smallest cluster that becomes a proposal.
    """

    role_weights: dict[str, float] = field(
        default_factory=lambda: {"address": 3.0, "subject": 3.0, "date": 1.0, "body": 2.0}
    )
    position_scale: float = 72.0
    closeness_share: float = 0.6
    width_tolerance: float = 18.0
    low_fit_threshold: float = 0.8
    tie_margin: float = 0.05
    cluster_distance: float = 36.0
    cluster_min_letters: float = 2

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view, in the same shape as the config file."""
        return {
            "role_weights": dict(self.role_weights),
            "position_scale": self.position_scale,
            "closeness_share": self.closeness_share,
            "width_tolerance": self.width_tolerance,
            "low_fit_threshold": self.low_fit_threshold,
            "tie_margin": self.tie_margin,
            "cluster_distance": self.cluster_distance,
            "cluster_min_letters": self.cluster_min_letters,
        }


_NUMBER_RULES: dict[str, tuple[float, float, str]] = {
    # key: (minimum, maximum, description)
    "position_scale": (1.0, 2000.0, "a distance in points, 1 to 2000"),
    "closeness_share": (0.0, 1.0, "a share between 0 and 1"),
    "width_tolerance": (0.0, 500.0, "a distance in points, 0 to 500"),
    "low_fit_threshold": (0.0, 1.0, "a score between 0 and 1"),
    "tie_margin": (0.0, 1.0, "a score difference between 0 and 1"),
    "cluster_distance": (0.0, 2000.0, "a distance in points, 0 to 2000"),
    "cluster_min_letters": (1.0, 10000.0, "a number of letters, 1 or more"),
}


def default_config_path() -> Path:
    """The bundled default settings file."""
    return Path(__file__).resolve().parent / "data" / "scoring.json"


def _number(value: Any, key: str, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{source}: '{key}' must be a number, got {value!r}")
    return float(value)


def config_from_dict(data: dict[str, Any], source: str = "<dict>") -> ScoringConfig:
    """Build a config from parsed JSON; missing keys keep their defaults.

    Raises:
        ConfigError: On unknown keys, wrong types or out-of-range values.
    """
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: expected a JSON object at the top level")
    base = ScoringConfig()
    changes: dict[str, Any] = {}
    allowed = {"role_weights", *_NUMBER_RULES}
    for key, value in data.items():
        if key.startswith("_"):  # "_comment" and friends
            continue
        if key not in allowed:
            raise ConfigError(
                f"{source}: unknown setting '{key}'. Known: {', '.join(sorted(allowed))}"
            )
        if key == "role_weights":
            if not isinstance(value, dict):
                raise ConfigError(f"{source}: 'role_weights' must be an object of role: weight")
            weights = dict(base.role_weights)
            for role, w in value.items():
                if role not in ROLES:
                    raise ConfigError(f"{source}: role_weights has unknown role '{role}'")
                if role not in SCORED_ROLES:
                    scored = ", ".join(SCORED_ROLES)
                    raise ConfigError(f"{source}: role '{role}' is not scored; use one of {scored}")
                num = _number(w, f"role_weights.{role}", source)
                if num < 0:
                    raise ConfigError(f"{source}: 'role_weights.{role}' must not be negative")
                weights[role] = num
            if sum(weights.values()) <= 0:
                raise ConfigError(f"{source}: role_weights must not all be zero")
            changes[key] = weights
        else:
            lo, hi, what = _NUMBER_RULES[key]
            num = _number(value, key, source)
            if not lo <= num <= hi:
                raise ConfigError(f"{source}: '{key}' must be {what}, got {num:g}")
            changes[key] = num
    return replace(base, **changes)


def load_config(path: Path | str | None = None) -> ScoringConfig:
    """Load a scoring config file, or the defaults when ``path`` is ``None``."""
    if path is None:
        return ScoringConfig()
    p = Path(path)
    try:
        with p.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ConfigError(f"{p}: cannot read config ({exc.strerror or exc})") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{p}: not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})"
        ) from exc
    return config_from_dict(data, source=str(p))


DEFAULT_CONFIG = ScoringConfig()
