from __future__ import annotations

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.ports.quotes import QuoteRepository
from charteros.application.ports.tenders import TenderRepository
from charteros.domain.missions import MissionId
from charteros.domain.quotes import Quote, QuoteId
from charteros.domain.rfqs import RfqId
from charteros.domain.tenders import Tender, TenderStatus

_SEALED_PHASES = frozenset(
    {
        TenderStatus.DRAFT,
        TenderStatus.OPEN,
        TenderStatus.BEST_AND_FINAL,
    }
)


class TenderVisibilityPolicy:
    """Application-level sealed-bid confidentiality gate for non-tender read surfaces."""

    def __init__(
        self,
        *,
        tenders: TenderRepository,
        quotes: QuoteRepository,
    ) -> None:
        self._tenders = tenders
        self._quotes = quotes

    @staticmethod
    def hides_competitive_evidence(tender: Tender) -> bool:
        return tender.sealed_bid and tender.status in _SEALED_PHASES

    def assert_rfq_quotes_visible(self, rfq_id: RfqId) -> None:
        invitation = self._tenders.find_invitation_for_rfq(rfq_id)
        if invitation is None:
            return
        tender = self._tenders.get(invitation.tender_id)
        if tender is None:
            raise EntityNotFoundError("tender invitation references a missing tender")
        self._assert_visible(tender)

    def get_visible_quote(self, quote_id: QuoteId) -> Quote:
        quote = self._quotes.get(quote_id)
        if quote is None:
            raise EntityNotFoundError("quote does not exist")
        self.assert_rfq_quotes_visible(quote.rfq_id)
        return quote

    def assert_mission_comparison_visible(self, mission_id: MissionId) -> None:
        tender = self._tenders.get_for_mission(mission_id)
        if tender is not None:
            self._assert_visible(tender)

    def _assert_visible(self, tender: Tender) -> None:
        if self.hides_competitive_evidence(tender):
            raise EntityConflictError(
                "sealed tender commercial evidence is unavailable outside the tender workflow "
                "until the tender is closed"
            )
