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


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_profile_dict(data: Any) -> list[str]:
    """Check parsed JSON against the layout profile schema; return every problem found.

    The rules mirror ``data/layout-profile.schema.json`` (a test keeps them in step).
    Each message names the field, e.g. ``zones[1].width: must be greater than 0``.
    Zones that overrun the page are reported too, since they are almost always typos.
    """
    if not isinstance(data, dict):
        return ["the file must contain a JSON object"]
    problems: list[str] = []
    for key in ("id", "page", "zones"):
        if key not in data:
            problems.append(f"{key}: required but missing")
    if "id" in data and (not isinstance(data["id"], str) or not data["id"]):
        problems.append("id: must be a non-empty string")
    for key in ("name", "description"):
        if key in data and not isinstance(data[key], str):
            problems.append(f"{key}: must be a string")
    version = data.get("profile_version", PROFILE_VERSION)
    if version != PROFILE_VERSION or isinstance(version, bool):
        problems.append(f"profile_version: must be {PROFILE_VERSION}, got {version!r}")
    page_w = page_h = None
    page = data.get("page")
    if "page" in data:
        if not isinstance(page, dict):
            problems.append("page: must be an object with width and height")
        else:
            for dim in ("width", "height"):
                v = page.get(dim)
                if dim not in page:
                    problems.append(f"page.{dim}: required but missing")
                elif not _num(v) or v <= 0:
                    problems.append(f"page.{dim}: must be a number greater than 0, got {v!r}")
            if page.get("units", "pt") != "pt":
                problems.append(f"page.units: only 'pt' is supported, got {page['units']!r}")
            if _num(page.get("width")) and page["width"] > 0:
                page_w = float(page["width"])
            if _num(page.get("height")) and page["height"] > 0:
                page_h = float(page["height"])
    zones = data.get("zones")
    if "zones" in data:
        if not isinstance(zones, list) or not zones:
            problems.append("zones: must be a non-empty list")
        else:
            for i, z in enumerate(zones):
                problems.extend(_zone_problems(i, z, page_w, page_h))
    return problems


def _zone_problems(i: int, z: Any, page_w: float | None, page_h: float | None) -> list[str]:
    at = f"zones[{i}]"
    if not isinstance(z, dict):
        return [f"{at}: must be an object"]
    out: list[str] = []
    allowed = {"role", "x", "y", "width", "height", "max_lines", "required"}
    for key in sorted(set(z) - allowed):
        out.append(f"{at}.{key}: unknown field (allowed: {', '.join(sorted(allowed))})")
    role = z.get("role")
    if "role" not in z:
        out.append(f"{at}.role: required but missing")
    elif role not in ROLES:
        out.append(f"{at}.role: {role!r} is not one of {', '.join(ROLES)}")
    ok: dict[str, float] = {}
    for key, minimum_open in (("x", False), ("y", False), ("width", True), ("height", True)):
        if key not in z:
            out.append(f"{at}.{key}: required but missing")
            continue
        v = z[key]
        if not _num(v):
            out.append(f"{at}.{key}: must be a number, got {v!r}")
        elif minimum_open and v <= 0:
            out.append(f"{at}.{key}: must be greater than 0, got {v!r}")
        elif not minimum_open and v < 0:
            out.append(f"{at}.{key}: must not be negative, got {v!r}")
        else:
            ok[key] = float(v)
    ml = z.get("max_lines")
    if "max_lines" in z and (not isinstance(ml, int) or isinstance(ml, bool) or ml < 1):
        out.append(f"{at}.max_lines: must be a whole number of at least 1, got {ml!r}")
    if "required" in z and not isinstance(z["required"], bool):
        out.append(f"{at}.required: must be true or false, got {z['required']!r}")
    if page_w is not None and "x" in ok and "width" in ok and ok["x"] + ok["width"] > page_w + 0.01:
        out.append(
            f"{at}: right edge {ok['x'] + ok['width']:g} is beyond the page width {page_w:g}"
        )
    if (
        page_h is not None
        and "y" in ok
        and "height" in ok
        and ok["y"] + ok["height"] > page_h + 0.01
    ):
        out.append(
            f"{at}: bottom edge {ok['y'] + ok['height']:g} is beyond the page height {page_h:g}"
        )
    return out


def profile_from_dict(data: dict[str, Any], source: str = "<dict>") -> LayoutProfile:
    """Build a :class:`LayoutProfile` from parsed JSON.

    Raises:
        ProfileError: Listing every problem found, one per line, with the field name.
    """
    problems = validate_profile_dict(data)
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise ProfileError(f"{source}: invalid layout profile\n{detail}")
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
        id=data["id"],
        name=str(data.get("name", data["id"])),
        description=str(data.get("description", "")),
        page_width=float(page["width"]),
        page_height=float(page["height"]),
        zones=zones,
        profile_version=int(data.get("profile_version", PROFILE_VERSION)),
        extra={k: v for k, v in data.items() if k not in known},
    )


def load_profile(path: Path) -> LayoutProfile:
    """Load one layout profile JSON file."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ProfileError(f"{path}: cannot read layout profile ({exc.strerror or exc})") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"{path}: not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})"
        ) from exc
    return profile_from_dict(data, source=str(path))


def schema_path() -> Path:
    """The bundled JSON Schema for layout profiles."""
    return Path(__file__).resolve().parent / "data" / "layout-profile.schema.json"


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
