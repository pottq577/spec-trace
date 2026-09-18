from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import InvariantViolation
from .util import canonical_json_bytes, sha256_bytes


class ContentStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for_hash(self, digest: str, suffix: str = ".json") -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("digest must be a lowercase SHA-256 hex string")
        return self.root / digest[:2] / digest[2:4] / f"{digest}{suffix}"

    def put_bytes(self, payload: bytes, suffix: str = ".json") -> tuple[str, str]:
        digest = sha256_bytes(payload)
        target = self.path_for_hash(digest, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256_bytes(target.read_bytes()) != digest:
                raise InvariantViolation(f"content hash mismatch: {target}")
            return digest, str(target.relative_to(self.root.parent))

        fd, temporary = tempfile.mkstemp(prefix=f".{digest}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if sha256_bytes(Path(temporary).read_bytes()) != digest:
                raise InvariantViolation("temporary content hash mismatch")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return digest, str(target.relative_to(self.root.parent))

    def put_json(self, value: Any) -> tuple[str, str]:
        return self.put_bytes(canonical_json_bytes(value), ".json")

    def put_text(self, value: str, suffix: str = ".md") -> tuple[str, str]:
        return self.put_bytes(value.encode("utf-8"), suffix)

    def read_relative(self, relative_ref: str) -> bytes:
        path = self.root.parent / relative_ref
        return path.read_bytes()

    def read_json(self, relative_ref: str) -> Any:
        return json.loads(self.read_relative(relative_ref).decode("utf-8"))
