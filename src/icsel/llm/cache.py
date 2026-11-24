"""On-disk cache of model replies, so an unchanged letter is not paid for twice.

One small JSON file per reply, named by a hash of the task, the prompt version (a hash
of the template text), the backend, the model and the full input. Only replies that
passed validation are stored; failures never are.

Replies can quote letter text (field detection does), so treat the folder like the
letters themselves. The default location, ``out/.ai-cache``, is git-ignored.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path

#: Overrides the cache folder.
CACHE_ENV = "ICSEL_AI_CACHE_DIR"


def default_cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """``ICSEL_AI_CACHE_DIR``, or ``out/.ai-cache`` under the current folder."""
    override = (os.environ if env is None else env).get(CACHE_ENV)
    return Path(override) if override else Path("out") / ".ai-cache"


class ReplyCache:
    """Validated model replies stored as ``<folder>/<key>.json``."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    @staticmethod
    def key(*parts: str) -> str:
        """Stable hash of ``parts``; the separator cannot appear by accident in JSON."""
        return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        """The stored reply, or ``None`` if there is none or it cannot be read."""
        try:
            data = json.loads((self.folder / f"{key}.json").read_text(encoding="utf-8"))
            reply = data["reply"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return reply if isinstance(reply, str) else None

    def put(self, key: str, reply: str) -> None:
        """Store ``reply``. A cache that cannot be written is ignored, not fatal."""
        target = self.folder / f"{key}.json"
        temp = self.folder / f"{key}.{uuid.uuid4().hex}.tmp"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps({"reply": reply}), encoding="utf-8")
            temp.replace(target)
        except OSError:
            return
