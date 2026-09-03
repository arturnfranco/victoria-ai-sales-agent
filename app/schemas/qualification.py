"""Qualification evidence and fit contracts."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceLevel(str, Enum):
    """Strength of evidence explicitly supplied by the lead."""

    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class FitLevel(str, Enum):
    """Commercial qualification bands."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NO_FIT = "no_fit"


class EvidenceAssessment(BaseModel):
    """Evidence level and its auditable conversational basis."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level: EvidenceLevel
    evidence: str | None

    @model_validator(mode="after")
    def evidence_matches_level(self) -> "EvidenceAssessment":
        if self.level is EvidenceLevel.NONE and self.evidence is not None:
            raise ValueError("none evidence level must not include an evidence note")
        if self.level is not EvidenceLevel.NONE and not self.evidence:
            raise ValueError("non-zero evidence level requires an evidence note")
        return self


class QualificationEvidence(BaseModel):
    """Evidence for every weighted qualification dimension."""

    model_config = ConfigDict(extra="forbid")

    need: EvidenceAssessment
    financial_complexity: EvidenceAssessment
    readiness: EvidenceAssessment
    urgency: EvidenceAssessment
    service_fit: EvidenceAssessment

    @classmethod
    def empty(cls) -> "QualificationEvidence":
        empty = {"level": EvidenceLevel.NONE, "evidence": None}
        return cls(
            need=empty,
            financial_complexity=empty,
            readiness=empty,
            urgency=empty,
            service_fit=empty,
        )


class QualificationResult(BaseModel):
    """Deterministic score derived from conversational evidence."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    fit: FitLevel
    contributions: dict[str, int]
