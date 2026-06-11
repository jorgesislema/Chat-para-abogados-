from __future__ import annotations

from dataclasses import dataclass

import bcrypt


@dataclass
class CryptContext:
    schemes: list[str] | tuple[str, ...]
    deprecated: str = "auto"

    def hash(self, secret: str) -> str:
        return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def verify(self, secret: str, hash: str) -> bool:
        try:
            return bcrypt.checkpw(secret.encode("utf-8"), hash.encode("utf-8"))
        except ValueError:
            return False
