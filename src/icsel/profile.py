"""Layout profiles: the page geometry a letter is matched against.

A layout profile describes one layout template as a set of rectangular
zones (address box, subject box, body area, ...). Coordinates are in PDF
points (1/72 inch) with the origin at the TOP-LEFT corner of the page and
``y`` growing downwards, which is the convention pdfplumber uses.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .inputs import InputError, collect_files

PROFILE_VERSION = 1

#: Roles that a zone (layout) or a block (letter) can have.
ROLES = (
    "letterhead",
    "address",
    "date",
    "subject",
    "salutation",
    "body",
    "closing",
    "footer",
    "unknown",
)


class ProfileError(ValueError):
    """Raised when a layout profile file is malformed."""


@dataclass
class Zone:
    """A rectangular zone on the page, in points, top-left origin."""

    role: str
    x: float
    y: float
    width: float
    height: float
    max_lines: int | None = None
    required: bool = False

    @property
    def right(self) -> float:
        """X coordinate of the right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """Y coordinate of the bottom edge (distance from page top)."""
        return self.y + self.height


@dataclass
class LayoutProfile:
    """One layout template: page size plus zones."""

    id: str
    name: str
    page_width: float
    page_height: float
    zones: list[Zone]
    description: str = ""
    profile_version: int = PROFILE_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def zone(self, role: str) -> Zone | None:
        """Return the first zone with ``role``, or ``None``."""
        for z in self.zones:
            if z.role == role:
                return z
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the on-disk JSON structure."""
        zones = []
        for z in self.zones:
            d = asdict(z)
            if d["max_lines"] is None:
                del d["max_lines"]
            if not d["required"]:
                del d["required"]
            zones.append(d)
        out: dict[str, Any] = {
            "profile_version": self.profile_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "page": {"width": self.page_width, "height": self.page_height, "units": "pt"},
            "zones": zones,
        }
        out.update(self.extra)
        return out


def profile_from_dict(data: dict[str, Any], source: str = "<dict>") -> LayoutProfile:
    """Build a :class:`LayoutProfile` from parsed JSON, with basic checks."""
    try:
        page = data["page"]
        zones = [
            Zone(
                role=z["role"],
                x=float(z["x"]),
                y=float(z["y"]),
                width=float(z["width"]),
                height=float(z["height"]),
                max_lines=z.get("max_lines"),
                required=bool(z.get("required", False)),
            )
            for z in data["zones"]
        ]
        known = {"profile_version", "id", "name", "description", "page", "zones"}
        return LayoutProfile(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            description=str(data.get("description", "")),
            page_width=float(page["width"]),
            page_height=float(page["height"]),
            zones=zones,
            profile_version=int(data.get("profile_version", PROFILE_VERSION)),
            extra={k: v for k, v in data.items() if k not in known},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileError(f"{source}: invalid layout profile ({exc!r})") from exc


def load_profile(path: Path) -> LayoutProfile:
    """Load one layout profile JSON file."""
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        return profile_from_dict(json.load(fh), source=str(path))


def default_layouts_dir() -> Path:
    """Folder of the example layout profiles bundled with the package."""
    return Path(__file__).resolve().parent / "data" / "layouts"


def load_profiles(sources: Path | str | Iterable[Path | str] | None = None) -> list[LayoutProfile]:
    """Load layout profiles from files, folders or glob patterns.

    Args:
        sources: One path or several. Folders contribute their ``*.json`` files
            (sorted by name). ``None`` loads the bundled example layouts.

    Raises:
        ProfileError: If nothing is found, a file is invalid, or two profiles
            share an ``id``.
    """
    if sources is None:
        sources = [default_layouts_dir()]
    elif isinstance(sources, (str, Path)):
        sources = [sources]
    try:
        files = collect_files(sources, ".json")
    except InputError as exc:
        raise ProfileError(f"layout profiles: {exc}") from exc
    profiles: list[LayoutProfile] = []
    seen: dict[str, Path] = {}
    for f in files:
        prof = load_profile(f)
        if prof.id in seen:
            raise ProfileError(f"duplicate layout id {prof.id!r} in {seen[prof.id]} and {f}")
        seen[prof.id] = f
        profiles.append(prof)
    return profiles
