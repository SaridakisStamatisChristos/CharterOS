from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredResponse:
    request_hash: str
    status_code: int
    response_body: dict[str, object]


class IdempotencyRepository(Protocol):
    def lock(self, scope: str, key: str) -> None: ...

    def get(self, scope: str, key: str) -> StoredResponse | None: ...

    def add(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        status_code: int,
        response_body: dict[str, object],
    ) -> None: ...


def canonical_request_hash(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
