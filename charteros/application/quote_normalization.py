from __future__ import annotations

from charteros.application.exceptions import EntityNotFoundError
from charteros.application.ports.quotes import QuoteRepository
from charteros.domain.quotes import QuoteId
from charteros.domain.quotes.normalization import QuoteNormalization, normalize_quote


class QuoteNormalizationService:
    def __init__(self, *, quotes: QuoteRepository) -> None:
        self._quotes = quotes

    def normalize(self, quote_id: QuoteId) -> QuoteNormalization:
        quote = self._quotes.get(quote_id)
        if quote is None:
            raise EntityNotFoundError("quote does not exist")
        return normalize_quote(quote)
