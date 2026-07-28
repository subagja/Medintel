from dataclasses import dataclass


@dataclass(frozen=True)
class AssessmentScores:
    priority_score: float
    confidence_score: float
    recommended_priority: str
    recommended_confidence: str


def normalize_scale_1_to_5(value: int) -> float:
    """
    Mengubah skor 1–5 menjadi skala 0–1.

    1 menjadi 0.20
    5 menjadi 1.00
    """
    return min(max(value / 5.0, 0.0), 1.0)


def calculate_priority_score(
    *,
    urgency_score: int,
    impact_score: int,
    geographic_scope_score: int,
    development_speed_score: int,
    vulnerability_score: int,
) -> float:
    urgency = normalize_scale_1_to_5(
        urgency_score
    )
    impact = normalize_scale_1_to_5(
        impact_score
    )
    geographic_scope = normalize_scale_1_to_5(
        geographic_scope_score
    )
    development_speed = normalize_scale_1_to_5(
        development_speed_score
    )
    vulnerability = normalize_scale_1_to_5(
        vulnerability_score
    )

    score = (
        urgency * 0.25
        + impact * 0.30
        + geographic_scope * 0.15
        + development_speed * 0.15
        + vulnerability * 0.15
    )

    return round(
        min(max(score, 0.0), 1.0),
        4,
    )


def calculate_confidence_score(
    *,
    source_reliability_score: float,
    information_credibility_score: float,
    information_completeness_score: float,
    evidence_consistency_score: float,
) -> float:
    score = (
        source_reliability_score * 0.25
        + information_credibility_score * 0.35
        + information_completeness_score * 0.20
        + evidence_consistency_score * 0.20
    )

    return round(
        min(max(score, 0.0), 1.0),
        4,
    )


def derive_recommended_priority(
    priority_score: float,
) -> str:
    if priority_score >= 0.85:
        return "critical"

    if priority_score >= 0.70:
        return "high"

    if priority_score >= 0.45:
        return "medium"

    return "low"


def derive_recommended_confidence(
    confidence_score: float,
) -> str:
    if confidence_score >= 0.75:
        return "high"

    if confidence_score >= 0.50:
        return "medium"

    return "low"


def calculate_assessment_scores(
    *,
    urgency_score: int,
    impact_score: int,
    geographic_scope_score: int,
    development_speed_score: int,
    vulnerability_score: int,
    source_reliability_score: float,
    information_credibility_score: float,
    information_completeness_score: float,
    evidence_consistency_score: float,
) -> AssessmentScores:
    priority_score = calculate_priority_score(
        urgency_score=urgency_score,
        impact_score=impact_score,
        geographic_scope_score=(
            geographic_scope_score
        ),
        development_speed_score=(
            development_speed_score
        ),
        vulnerability_score=vulnerability_score,
    )

    confidence_score = calculate_confidence_score(
        source_reliability_score=(
            source_reliability_score
        ),
        information_credibility_score=(
            information_credibility_score
        ),
        information_completeness_score=(
            information_completeness_score
        ),
        evidence_consistency_score=(
            evidence_consistency_score
        ),
    )

    return AssessmentScores(
        priority_score=priority_score,
        confidence_score=confidence_score,
        recommended_priority=(
            derive_recommended_priority(
                priority_score
            )
        ),
        recommended_confidence=(
            derive_recommended_confidence(
                confidence_score
            )
        ),
    )