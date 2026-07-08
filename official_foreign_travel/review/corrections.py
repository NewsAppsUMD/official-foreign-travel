"""Corrections overlay: dotted/indexed-path edits into a report dict, persisted to disk."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_TOKEN_RE = re.compile(r"^([^.\[\]]+)(\[(\d+)\])?$")


def _parse_path(path: str) -> List[Tuple[str, Optional[int]]]:
    """Parse 'travelers[2].segments[0].costs.total' into
    [("travelers", 2), ("segments", 0), ("costs", None), ("total", None)]."""
    tokens = []
    for part in path.split("."):
        match = _TOKEN_RE.match(part)
        if not match:
            raise ValueError(f"Invalid path segment: {part!r} in path {path!r}")
        key, _, index = match.groups()
        tokens.append((key, int(index) if index is not None else None))
    return tokens


def get_path(data: Any, path: str) -> Any:
    """Read a value out of a JSON-shaped dict using a dotted/indexed path."""
    current = data
    for key, index in _parse_path(path):
        current = current[key]
        if index is not None:
            current = current[index]
    return current


def set_path(data: Any, path: str, value: Any) -> None:
    """Write a value into a JSON-shaped dict using a dotted/indexed path, in place."""
    tokens = _parse_path(path)
    current = data
    for key, index in tokens[:-1]:
        current = current[key]
        if index is not None:
            current = current[index]
    last_key, last_index = tokens[-1]
    if last_index is not None:
        current[last_key][last_index] = value
    else:
        current[last_key] = value


def load_corrections(path: Path) -> Dict[str, dict]:
    """Load the corrections overlay file, or return {} if it doesn't exist yet."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_report_correction(
    path: Path, report_id: str, status: str, edits: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Save (replacing) one report's correction entry, preserving all others.

    Returns:
        The entry that was just saved.
    """
    corrections = load_corrections(path)
    entry = {
        "status": status,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "edits": edits,
    }
    corrections[report_id] = entry
    path.write_text(json.dumps(corrections, indent=2), encoding="utf-8")
    return entry
