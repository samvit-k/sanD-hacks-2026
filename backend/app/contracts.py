"""Shared contracts.

1. Gemini response schemas (what the model must return).
2. The vocabularies used everywhere (labels, impacts, condition fields).

The JSON sent to the frontend (graph, impact summary) is built in graph.py; the
example files in frontend/public/fixtures/ are generated from that same code.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

LABELS = ("supports", "genuine_contradiction", "apparent_contradiction", "not_comparable", "unresolved")
IMPACTS = ("strengthens", "challenges", "qualifies", "leaves_unresolved")
# Fields compared deterministically to build condition_diff (descriptive only, never decides a label).
CONDITION_FIELDS = ("population", "sample_size", "method", "duration", "outcome_measure")
NOT_REPORTED = "not reported"

Label = Literal["supports", "genuine_contradiction", "apparent_contradiction", "not_comparable", "unresolved"]
Impact = Literal["strengthens", "challenges", "qualifies", "leaves_unresolved"]
COMPARABILITY = ("same_hypothesis_same_measure", "same_hypothesis_different_setting", "thematic_only")
Comparability = Literal["same_hypothesis_same_measure", "same_hypothesis_different_setting", "thematic_only"]

class Conditions(BaseModel):
    population: Optional[str] = Field(None, description="Who was studied; null if not reported")
    sample_size: Optional[str] = Field(None, description="e.g. 'n=120'; null if not reported")
    method: Optional[str] = Field(None, description="Study design, e.g. 'randomized controlled trial'")
    duration: Optional[str] = Field(None, description="Intervention/follow-up length, e.g. '12 weeks'")
    outcome_measure: Optional[str] = Field(None, description="What was measured, e.g. 'standardized test score'")
    effect_size: Optional[str] = None
    significance: Optional[str] = Field(None, description="e.g. 'p<0.01' or 'not significant'")
    limitations: Optional[str] = Field(None, description="Limitations the authors state")

class ExtractedClaim(BaseModel):
    text: str = Field(description="One testable finding, stated faithfully and concisely")
    sentence_ids: List[str] = Field(description="Full IDs of the sentences that state this finding")
    conditions: Conditions

class ExtractionResult(BaseModel):
    claims: List[ExtractedClaim]
    no_findings_note: Optional[str] = Field(
        None, description="Only when returning fewer claims than asked (especially none): one sentence on why, "
                          "e.g. the paper does not study the research question's topic. Not evidence.")

class PairJudgment(BaseModel):
    comparability: Optional[Comparability] = Field(
        None, description="Decide this FIRST. same_hypothesis_same_measure: both test the same hypothesis with the "
                          "same (or equivalent) outcome measure. same_hypothesis_different_setting: same hypothesis, "
                          "but different dataset, population, setting or metric. thematic_only: related topic, but "
                          "they test different hypotheses (e.g. different interventions or mechanisms).")
    label: Label
    explanation: str = Field(description="2-3 sentences grounded in the cited sentences")
    key_differences: List[str] = Field(description="Condition differences that matter for this judgment; may be empty")
    cited_sentence_ids: List[str] = Field(description="Full sentence IDs from BOTH papers supporting the judgment")

class ImpactJudgment(BaseModel):
    impact: Impact
    rationale: str = Field(description="2-3 sentences grounded in the cited sentences")
    cited_sentence_ids: List[str] = Field(description="Full sentence IDs from BOTH papers supporting the judgment")

class QuestionProposal(BaseModel):
    question: str = Field(description="ONE focused, neutral central research question ending with '?'. "
                                      "It asks; it never states or implies a finding.")
    rationale: str = Field(description="One sentence on how the question focuses the user's interest (what is "
                                       "compared, for whom, which outcome). Mention no study, author, year, "
                                       "statistic or result.")

class ChatAnswer(BaseModel):
    answer: str = Field(description="Answer grounded ONLY in the provided evidence. Refer to graph items with "
                                    "tokens exactly as given, e.g. [[claim:12]], [[paper:abc-123456]], [[rel:5]], [[q]]")
    cited_sentence_ids: List[str] = Field(description="Full IDs of the provided source sentences the answer relies on")
    insufficient_evidence: bool = Field(description="True if the provided evidence cannot answer the question")