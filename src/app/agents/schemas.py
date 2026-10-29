"""Pydantic schemas for what each agent must return. A validation failure triggers a retry."""
from typing import Literal

from pydantic import BaseModel, Field

ReasonCode = Literal["CLEAN_MATCH", "QTY_SHORT", "PRICE_VARIANCE", "GST_MISMATCH", "DUPLICATE",
                     "BANK_CHANGE_RISK", "VENDOR_BLOCKED", "MISSING_GRN", "UNKNOWN_PO"]
SpecialistName = Literal["delivery_specialist", "pricing_tax_specialist", "vendor_risk_specialist",
                         "document_specialist"]


class Plan(BaseModel):
    """Orchestrator step 1: which specialists to call and why."""
    specialists: list[SpecialistName] = Field(min_length=1)
    reasons: dict[str, str] = Field(description="specialist name -> why it is needed for this invoice")
    skipped: dict[str, str] = Field(default_factory=dict, description="specialist name -> why it is not needed")


class Issue(BaseModel):
    reason_code: ReasonCode
    line_ids: list[str] = Field(default_factory=list)
    summary: str


class SpecialistReport(BaseModel):
    """What every specialist returns."""
    specialist: str = ""   # filled in by code; models kept inventing names here
    issues: list[Issue] = Field(default_factory=list, description="empty if every check passed")
    citations: list[str] = Field(default_factory=list)
    notes: str = ""


class FinalDecision(BaseModel):
    """Orchestrator step 3: the decision."""
    decision: Literal["pay", "hold", "escalate"]
    reason_code: ReasonCode
    other_reasons: list[ReasonCode] = Field(default_factory=list)
    needs_human: bool
    summary: str
