"""Corrections overlay: dotted/indexed-path edits into a report dict, persisted to disk."""

import re
from typing import Any, List, Optional, Tuple

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
