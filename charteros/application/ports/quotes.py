from __future__ import annotations

from typing import Protocol

from charteros.domain.quotes import Quote, QuoteId
from charteros.domain.rfqs import RfqId


class QuoteRepository(Protocol):
    def add(self, quote: Quote) -> None: ...

    def get(self, quote_id: QuoteId) -> Quote | None: ...

    def get_for_update(self, quote_id: QuoteId) -> Quote | None: ...

    def get_current_for_rfq(self, rfq_id: RfqId) -> Quote | None: ...

    def list_for_rfq(self, rfq_id: RfqId) -> tuple[Quote, ...]: ...

    def save(self, quote: Quote, *, expected_version: int) -> None: ...
