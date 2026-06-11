from __future__ import annotations

from typing import Any, Iterable, Mapping

def encode(
    claims: Mapping[str, Any],
    key: str,
    algorithm: str,
) -> str: ...

def decode(
    token: str,
    key: str,
    algorithms: Iterable[str],
) -> dict[str, Any]: ...
