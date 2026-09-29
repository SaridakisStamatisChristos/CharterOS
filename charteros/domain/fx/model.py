from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from enum import StrEnum

from charteros.domain.missions import MissionId
from charteros.domain.organizations import OrganizationId
from charteros.domain.procurement_approvals import ProcurementApprovalId
from charteros.domain.quotes import QuoteId
from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId
from charteros.domain.shared.money import Money

CONVERSION_POLICY_VERSION = "fx-conversion-v1"
ROUNDING_POLICY_VERSION = "half-even-target-minor-v1"
LOCK_POLICY_VERSION = "fx-lock-v1"
FX_LOCK_TTL_SECONDS = 30
MAX_MINOR_EXPONENT = 9
MAX_RATE_SCALE = 18


class FxRateId(TypedId):
    __slots__ = ()


class FxLockId(TypedId):
    __slots__ = ()


class FxLockStatus(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _bounded_text(value: str, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise DomainValidationError(f"{field_name} cannot be blank")
    if len(normalized) > max_length:
        raise DomainValidationError(f"{field_name} cannot exceed {max_length} characters")
    return normalized


def _minor_exponent(value: int, *, field_name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_MINOR_EXPONENT
    ):
        raise DomainValidationError(
            f"{field_name} must be an integer between 0 and {MAX_MINOR_EXPONENT}"
        )
    return value


def canonical_rate_text(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise DomainValidationError("FX rate must be a non-empty canonical decimal string")
    if "e" in value.lower():
        raise DomainValidationError("FX rate must use plain decimal notation")
    try:
        rate = Decimal(value)
    except InvalidOperation as exc:
        raise DomainValidationError("FX rate must be a valid decimal string") from exc
    if not rate.is_finite() or rate <= 0:
        raise DomainValidationError("FX rate must be finite and strictly positive")
    exponent = rate.as_tuple().exponent
    if not isinstance(exponent, int):
        raise DomainValidationError("FX rate exponent must be finite")
    scale = max(0, -exponent)
    if scale > MAX_RATE_SCALE:
        raise DomainValidationError(
            f"FX rate cannot exceed {MAX_RATE_SCALE} fractional decimal places"
        )
    normalized = format(rate.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


@dataclass(frozen=True, slots=True)
class FxConversion:
    original: Money
    converted: Money
    rate_id: FxRateId | None
    rate_text: str
    fx_source: str
    fx_source_version: str
    fx_timestamp: datetime
    rate_recorded_at: datetime
    source_minor_exponent: int | None
    target_minor_exponent: int | None
    conversion_policy_version: str = CONVERSION_POLICY_VERSION
    rounding_policy: str = ROUNDING_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class FxLockedQuote:
    quote_id: QuoteId
    quote_revision_number: int
    original_expected: Money
    original_worst_case: Money
    converted_expected: Money
    converted_worst_case: Money
    rate_id: FxRateId | None
    rate_text: str
    fx_source: str
    fx_source_version: str
    fx_timestamp: datetime
    rate_recorded_at: datetime
    source_minor_exponent: int | None
    target_minor_exponent: int | None
    global_rank: int
    global_score_method: str
    global_score_total_basis_points: int

    def __post_init__(self) -> None:
        if self.quote_revision_number < 1:
            raise DomainValidationError("locked quote revision must be positive")
        if self.original_expected.currency != self.original_worst_case.currency:
            raise DomainValidationError("locked original totals must share a currency")
        if self.converted_expected.currency != self.converted_worst_case.currency:
            raise DomainValidationError("locked converted totals must share a currency")
        if self.global_rank < 1:
            raise DomainValidationError("global FX rank must be positive")
        if self.global_score_total_basis_points < 0:
            raise DomainValidationError("global FX score cannot be negative")


class FxRateObservation(AggregateRoot[FxRateId]):
    """Immutable FX market observation or correction revision."""

    aggregate_type = "fx_rate"

    def __init__(
        self,
        rate_id: FxRateId,
        *,
        source_currency: Currency,
        target_currency: Currency,
        rate_text: str,
        source_minor_exponent: int,
        target_minor_exponent: int,
        fx_source: str,
        fx_source_version: str,
        fx_timestamp: datetime,
        recorded_at: datetime,
        revision_number: int,
        supersedes_rate_id: FxRateId | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(rate_id, version=version)
        if source_currency == target_currency:
            raise DomainValidationError("FX rate currencies must differ")
        if revision_number < 1:
            raise DomainValidationError("FX rate revision_number must be positive")
        if revision_number == 1 and supersedes_rate_id is not None:
            raise DomainValidationError("first FX rate revision cannot supersede another rate")
        if revision_number > 1 and supersedes_rate_id is None:
            raise DomainValidationError("corrected FX rate revision must supersede a prior rate")
        if supersedes_rate_id == rate_id:
            raise DomainValidationError("FX rate cannot supersede itself")

        self.source_currency = source_currency
        self.target_currency = target_currency
        self.rate_text = canonical_rate_text(rate_text)
        self.source_minor_exponent = _minor_exponent(
            source_minor_exponent, field_name="source_minor_exponent"
        )
        self.target_minor_exponent = _minor_exponent(
            target_minor_exponent, field_name="target_minor_exponent"
        )
        self.fx_source = _bounded_text(fx_source, field_name="fx_source", max_length=64)
        self.fx_source_version = _bounded_text(
            fx_source_version,
            field_name="fx_source_version",
            max_length=128,
        )
        self.fx_timestamp = _utc(fx_timestamp, field_name="fx_timestamp")
        self.recorded_at = _utc(recorded_at, field_name="recorded_at")
        if self.fx_timestamp > self.recorded_at:
            raise DomainValidationError("FX timestamp cannot be later than recorded_at")
        self.revision_number = revision_number
        self.supersedes_rate_id = supersedes_rate_id

    @classmethod
    def record(
        cls,
        *,
        source_currency: Currency,
        target_currency: Currency,
        rate_text: str,
        source_minor_exponent: int,
        target_minor_exponent: int,
        fx_source: str,
        fx_source_version: str,
        fx_timestamp: datetime,
        recorded_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> FxRateObservation:
        observation = cls(
            FxRateId.new(),
            source_currency=source_currency,
            target_currency=target_currency,
            rate_text=rate_text,
            source_minor_exponent=source_minor_exponent,
            target_minor_exponent=target_minor_exponent,
            fx_source=fx_source,
            fx_source_version=fx_source_version,
            fx_timestamp=fx_timestamp,
            recorded_at=recorded_at,
            revision_number=1,
        )
        observation._record_event(
            "FX_RATE_RECORDED",
            observation.event_payload(),
            correlation_id=correlation_id,
            occurred_at=observation.recorded_at,
        )
        return observation

    @classmethod
    def correction(
        cls,
        *,
        previous: FxRateObservation,
        rate_text: str,
        fx_source_version: str,
        recorded_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> FxRateObservation:
        when = _utc(recorded_at, field_name="recorded_at")
        if when <= previous.recorded_at:
            raise DomainValidationError(
                "FX correction recorded_at must be later than the superseded revision"
            )
        correction = cls(
            FxRateId.new(),
            source_currency=previous.source_currency,
            target_currency=previous.target_currency,
            rate_text=rate_text,
            source_minor_exponent=previous.source_minor_exponent,
            target_minor_exponent=previous.target_minor_exponent,
            fx_source=previous.fx_source,
            fx_source_version=fx_source_version,
            fx_timestamp=previous.fx_timestamp,
            recorded_at=when,
            revision_number=previous.revision_number + 1,
            supersedes_rate_id=previous.id,
        )
        correction._record_event(
            "FX_RATE_CORRECTED",
            correction.event_payload(),
            correlation_id=correlation_id,
            occurred_at=when,
        )
        return correction

    def event_payload(self) -> dict[str, object]:
        return {
            "source_currency": str(self.source_currency),
            "target_currency": str(self.target_currency),
            "rate": self.rate_text,
            "source_minor_exponent": self.source_minor_exponent,
            "target_minor_exponent": self.target_minor_exponent,
            "fx_source": self.fx_source,
            "fx_source_version": self.fx_source_version,
            "fx_timestamp": self.fx_timestamp.isoformat().replace("+00:00", "Z"),
            "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            "revision_number": self.revision_number,
            "supersedes_rate_id": (
                str(self.supersedes_rate_id) if self.supersedes_rate_id is not None else None
            ),
        }


def convert_money(
    *,
    amount: Money,
    rate: FxRateObservation,
    base_currency: Currency,
) -> FxConversion:
    if amount.currency != rate.source_currency:
        raise DomainValidationError("FX rate source currency does not match amount currency")
    if base_currency != rate.target_currency:
        raise DomainValidationError(
            "FX rate target currency does not match requested base currency"
        )

    with localcontext() as context:
        context.prec = 80
        decimal_rate = Decimal(rate.rate_text)
        exponent_delta = rate.target_minor_exponent - rate.source_minor_exponent
        exact_minor = Decimal(amount.amount_minor) * decimal_rate * (Decimal(10) ** exponent_delta)
        rounded_minor = exact_minor.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)

    converted = Money(int(rounded_minor), base_currency)
    return FxConversion(
        original=amount,
        converted=converted,
        rate_id=rate.id,
        rate_text=rate.rate_text,
        fx_source=rate.fx_source,
        fx_source_version=rate.fx_source_version,
        fx_timestamp=rate.fx_timestamp,
        rate_recorded_at=rate.recorded_at,
        source_minor_exponent=rate.source_minor_exponent,
        target_minor_exponent=rate.target_minor_exponent,
    )


def identity_conversion(*, amount: Money, at: datetime) -> FxConversion:
    when = _utc(at, field_name="identity conversion timestamp")
    return FxConversion(
        original=amount,
        converted=amount,
        rate_id=None,
        rate_text="1",
        fx_source="identity",
        fx_source_version=CONVERSION_POLICY_VERSION,
        fx_timestamp=when,
        rate_recorded_at=when,
        source_minor_exponent=None,
        target_minor_exponent=None,
    )


class FxLock(AggregateRoot[FxLockId]):
    """Short-lived executable buyer FX commitment over exact quote revisions."""

    aggregate_type = "fx_lock"

    def __init__(
        self,
        lock_id: FxLockId,
        *,
        buyer_id: OrganizationId,
        mission_id: MissionId,
        base_currency: Currency,
        fx_source: str,
        locked_at: datetime,
        expires_at: datetime,
        entries: tuple[FxLockedQuote, ...],
        integrity_digest: str,
        status: FxLockStatus = FxLockStatus.ACTIVE,
        consumed_at: datetime | None = None,
        consumed_approval_id: ProcurementApprovalId | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(lock_id, version=version)
        self.buyer_id = buyer_id
        self.mission_id = mission_id
        self.base_currency = base_currency
        self.fx_source = _bounded_text(fx_source, field_name="fx_source", max_length=64)
        self.locked_at = _utc(locked_at, field_name="locked_at")
        self.expires_at = _utc(expires_at, field_name="expires_at")
        if self.expires_at <= self.locked_at:
            raise DomainValidationError("FX lock expires_at must be later than locked_at")
        if not entries:
            raise DomainValidationError("FX lock requires at least one eligible quote")
        quote_ids = [entry.quote_id for entry in entries]
        if len(set(quote_ids)) != len(quote_ids):
            raise DomainValidationError("FX lock cannot contain duplicate quotes")
        if any(entry.converted_expected.currency != base_currency for entry in entries):
            raise DomainValidationError("FX lock entries must use the lock base currency")
        if len(integrity_digest) != 64:
            raise DomainValidationError("FX lock integrity digest must be a SHA-256 hex digest")
        try:
            int(integrity_digest, 16)
        except ValueError as exc:
            raise DomainValidationError("FX lock integrity digest must be hexadecimal") from exc

        self.entries = entries
        self.integrity_digest = integrity_digest.lower()
        self.status = FxLockStatus(status)
        self.consumed_at = (
            _utc(consumed_at, field_name="consumed_at") if consumed_at is not None else None
        )
        self.consumed_approval_id = consumed_approval_id
        self._validate_state()

    def _validate_state(self) -> None:
        if self.status is FxLockStatus.ACTIVE:
            if self.consumed_at is not None or self.consumed_approval_id is not None:
                raise DomainValidationError("active FX lock cannot have consumption evidence")
        elif self.consumed_at is None or self.consumed_approval_id is None:
            raise DomainValidationError("consumed FX lock requires consumption evidence")

    @classmethod
    def create(
        cls,
        *,
        buyer_id: OrganizationId,
        mission_id: MissionId,
        base_currency: Currency,
        fx_source: str,
        locked_at: datetime,
        entries: tuple[FxLockedQuote, ...],
        integrity_digest: str,
        correlation_id: CorrelationId | None = None,
    ) -> FxLock:
        when = _utc(locked_at, field_name="locked_at")
        lock = cls(
            FxLockId.new(),
            buyer_id=buyer_id,
            mission_id=mission_id,
            base_currency=base_currency,
            fx_source=fx_source,
            locked_at=when,
            expires_at=when + timedelta(seconds=FX_LOCK_TTL_SECONDS),
            entries=entries,
            integrity_digest=integrity_digest,
        )
        lock._record_event(
            "FX_LOCK_CREATED",
            {
                "buyer_id": str(buyer_id),
                "mission_id": str(mission_id),
                "base_currency": str(base_currency),
                "fx_source": lock.fx_source,
                "locked_at": lock.locked_at.isoformat().replace("+00:00", "Z"),
                "expires_at": lock.expires_at.isoformat().replace("+00:00", "Z"),
                "lock_policy_version": LOCK_POLICY_VERSION,
                "conversion_policy_version": CONVERSION_POLICY_VERSION,
                "rounding_policy": ROUNDING_POLICY_VERSION,
                "integrity_digest": lock.integrity_digest,
                "quote_ids": [str(entry.quote_id) for entry in lock.entries],
            },
            actor_id=buyer_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )
        return lock

    def entry_for_quote(self, quote_id: QuoteId) -> FxLockedQuote | None:
        return next((entry for entry in self.entries if entry.quote_id == quote_id), None)

    def assert_usable(
        self,
        *,
        buyer_id: OrganizationId,
        mission_id: MissionId,
        quote_id: QuoteId,
        quote_revision_number: int,
        at: datetime,
    ) -> FxLockedQuote:
        when = _utc(at, field_name="FX lock validation time")
        if self.buyer_id != buyer_id or self.mission_id != mission_id:
            raise DomainValidationError("FX lock does not belong to this buyer mission")
        if self.status is not FxLockStatus.ACTIVE:
            raise DomainValidationError("FX lock has already been consumed")
        if when < self.locked_at or when >= self.expires_at:
            raise DomainValidationError("FX lock has expired")
        entry = self.entry_for_quote(quote_id)
        if entry is None:
            raise DomainValidationError("FX lock does not contain the selected quote")
        if entry.quote_revision_number != quote_revision_number:
            raise DomainValidationError("FX lock quote revision is stale")
        return entry

    def consume(
        self,
        *,
        approval_id: ProcurementApprovalId,
        consumed_at: datetime,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        when = _utc(consumed_at, field_name="consumed_at")
        if self.status is not FxLockStatus.ACTIVE:
            raise DomainValidationError("only an active FX lock can be consumed")
        if when < self.locked_at or when >= self.expires_at:
            raise DomainValidationError("FX lock cannot be consumed outside its lock window")
        self.status = FxLockStatus.CONSUMED
        self.consumed_at = when
        self.consumed_approval_id = approval_id
        self._record_event(
            "FX_LOCK_CONSUMED",
            {
                "approval_id": str(approval_id),
                "consumed_at": when.isoformat().replace("+00:00", "Z"),
            },
            actor_id=self.buyer_id,
            correlation_id=correlation_id,
            occurred_at=when,
        )
