"""Pydantic schemas = structured outputs of every agent."""
from typing import Literal

from pydantic import BaseModel, Field


class PICO(BaseModel):
    population: str
    intervention: str
    comparator: str
    outcome: str


class Protocol(BaseModel):
    title: str
    objective: str
    pico: PICO
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]
    keywords: list[str]
    study_designs: list[str] = Field(default_factory=list)
    year_from: int | None = None


class SearchStrategy(BaseModel):
    queries: list[str] = Field(description="3 to 5 Boolean queries (AND/OR, quoted phrases)")
    mesh_terms: list[str] = Field(default_factory=list)
    rationale: str


class ScreenDecision(BaseModel):
    decision: Literal["include", "exclude"]
    reason: str = Field(description="1-2 sentences citing the criteria")
    confidence: float = Field(description="0.0 to 1.0")


class Extraction(BaseModel):
    eligible: bool = Field(description="Does the study meet the protocol on the available text?")
    ineligible_reason: str = ""
    study_design: str = "not reported"
    population: str = "not reported"
    sample_size: str = "not reported"
    intervention: str = "not reported"
    comparator: str = "not reported"
    outcomes: str = "not reported"
    main_findings: str = "not reported"
    risk_of_bias: Literal["low", "some concerns", "high", "unclear"] = "unclear"
    rob_rationale: str = ""
    evidence_quote: str = Field(
        default="", description="Short VERBATIM quote from the context supporting main_findings"
    )


class CriticReport(BaseModel):
    verdict: Literal["pass", "revise"]
    score: int = Field(description="Overall quality 0-10")
    issues: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    protocol_adherence: str = ""
