from charteros.application.data_governance import (
    DATA_ASSET_POLICIES,
    DATA_ASSET_POLICY_BY_TABLE,
    RetentionAction,
    assert_automated_deletion_allowed,
)
from charteros.application.exceptions import EntityConflictError
from charteros.infrastructure.db.base import Base
from charteros.infrastructure.db.models import load_models


def test_every_orm_table_has_exactly_one_data_governance_policy() -> None:
    load_models()
    persisted_tables = set(Base.metadata.tables)
    classified_tables = {item.table for item in DATA_ASSET_POLICIES}

    assert len(DATA_ASSET_POLICIES) == len(classified_tables)
    assert classified_tables == persisted_tables


def test_only_transient_pr44_state_allows_automated_deletion() -> None:
    allowed = {
        item.table for item in DATA_ASSET_POLICIES if item.automated_delete_allowed
    }
    assert allowed == {"idempotency_records", "api_rate_limit_windows"}
    for table in allowed:
        assert (
            DATA_ASSET_POLICY_BY_TABLE[table].retention_action
            is RetentionAction.POLICY_TTL_DELETE
        )
        assert_automated_deletion_allowed(table)


def test_authoritative_evidence_cannot_be_automatically_deleted() -> None:
    for table in ("bookings", "quotes", "contracts", "outbox_events", "evidence_integrity_entries"):
        try:
            assert_automated_deletion_allowed(table)
        except EntityConflictError:
            pass
        else:
            raise AssertionError(f"{table} unexpectedly permits automated deletion")
