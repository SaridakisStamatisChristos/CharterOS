from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType

from charteros.matching.types import (
    COMPONENT_POINTS,
    CandidateEvaluation,
    MatchDraft,
    MatchReasonCode,
    RankedMatch,
    ScoreDecomposition,
)


def _normalized_points(
    value: int,
    values: Sequence[int],
    *,
    lower_is_better: bool,
) -> int:
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        return COMPONENT_POINTS
    numerator = maximum - value if lower_is_better else value - minimum
    points = (
        Decimal(COMPONENT_POINTS)
        * Decimal(numerator)
        / Decimal(maximum - minimum)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(points)


def rank_matches(drafts: Sequence[MatchDraft]) -> tuple[RankedMatch, ...]:
    if not drafts:
        return ()

    deadheads = [item.reposition_distance_tenths_nm for item in drafts]
    costs = [item.estimated_operating_cost.amount_minor for item in drafts]
    buffers = [item.timing_buffer_minutes for item in drafts]
    risks = [item.schedule_risk_basis_points for item in drafts]

    scored: list[tuple[MatchDraft, ScoreDecomposition]] = []
    for draft in drafts:
        deadhead_points = _normalized_points(
            draft.reposition_distance_tenths_nm,
            deadheads,
            lower_is_better=True,
        )
        operating_cost_points = _normalized_points(
            draft.estimated_operating_cost.amount_minor,
            costs,
            lower_is_better=True,
        )
        timing_points = _normalized_points(
            draft.timing_buffer_minutes,
            buffers,
            lower_is_better=False,
        )
        risk_points = _normalized_points(
            draft.schedule_risk_basis_points,
            risks,
            lower_is_better=True,
        )
        total = deadhead_points + operating_cost_points + timing_points + risk_points
        scored.append(
            (
                draft,
                ScoreDecomposition(
                    method="equal_weight_minmax_v1",
                    total_basis_points=total,
                    deadhead_points=deadhead_points,
                    operating_cost_points=operating_cost_points,
                    timing_buffer_points=timing_points,
                    schedule_risk_points=risk_points,
                ),
            )
        )

    scored.sort(
        key=lambda item: (
            -item[1].total_basis_points,
            item[0].reposition_distance_tenths_nm,
            -item[0].timing_buffer_minutes,
            item[0].aircraft_id.value.hex,
        )
    )
    return tuple(
        RankedMatch(rank=index, draft=draft, score=score)
        for index, (draft, score) in enumerate(scored, start=1)
    )


def rejection_summary(
    evaluations: Sequence[CandidateEvaluation],
) -> Mapping[MatchReasonCode, int]:
    counter: Counter[MatchReasonCode] = Counter()
    for evaluation in evaluations:
        counter.update(evaluation.rejection_reasons)
    return MappingProxyType(
        dict(sorted(counter.items(), key=lambda item: item[0].value))
    )
