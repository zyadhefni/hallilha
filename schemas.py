"""Pydantic schemas. Every LLM output is validated before the pipeline acts on it."""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- understand
class ColumnMeaning(BaseModel):
    name: str
    meaning: str = ""
    role: Literal["id", "date", "metric", "category", "text", "other"] = "other"


class Understanding(BaseModel):
    goal: str
    dataset_description: str = ""
    main_metric: Optional[str] = None
    columns: list[ColumnMeaning] = []
    suspected_issues: list[str] = []
    assumptions: list[str] = []


# ---------------------------------------------------------------- cleaning DSL
# The model never edits data. It proposes operations from this closed vocabulary;
# deterministic code executes and verifies each one.
class _Op(BaseModel):
    reason: str = ""


class DropExactDuplicates(_Op):
    op: Literal["drop_exact_duplicates"]


class StripWhitespace(_Op):
    op: Literal["strip_whitespace"]
    columns: list[str]


class StandardizeMissing(_Op):
    op: Literal["standardize_missing"]
    columns: list[str]
    tokens: list[str]


class ParseNumeric(_Op):
    op: Literal["parse_numeric"]
    column: str


class ParseDate(_Op):
    op: Literal["parse_date"]
    column: str
    dayfirst: bool = False


class NormalizeCategories(_Op):
    op: Literal["normalize_categories"]
    column: str
    case: Optional[Literal["lower", "upper", "title"]] = None
    mapping: dict[str, str] = {}


class Impute(_Op):
    op: Literal["impute"]
    column: str
    strategy: Literal["median", "mean", "mode", "constant"]
    value: Optional[str] = None


class DropRowsMissing(_Op):
    op: Literal["drop_rows_missing"]
    columns: list[str]


class FlagOutliers(_Op):
    op: Literal["flag_outliers"]
    column: str
    k: float = 3.0


class SetInvalidToMissing(_Op):
    op: Literal["set_invalid_to_missing"]
    column: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class DropColumns(_Op):
    op: Literal["drop_columns"]
    columns: list[str]


CleanOp = Annotated[
    Union[DropExactDuplicates, StripWhitespace, StandardizeMissing, ParseNumeric, ParseDate,
          NormalizeCategories, Impute, DropRowsMissing, FlagOutliers, SetInvalidToMissing, DropColumns],
    Field(discriminator="op"),
]


class CleanPlanRaw(BaseModel):
    """Ops arrive as dicts so one malformed op cannot invalidate the whole plan."""
    ops: list[dict]
    notes: str = ""


# ---------------------------------------------------------------- analysis
class AnalysisItem(BaseModel):
    title: str
    question: str = ""
    code: str


class AnalysisPlan(BaseModel):
    analyses: list[AnalysisItem]


class AnalysisFix(BaseModel):
    index: int
    code: str


class AnalysisRepair(BaseModel):
    fixed: list[AnalysisFix]


class FindingItem(BaseModel):
    index: int
    finding: str


class Interpretation(BaseModel):
    findings: list[FindingItem]


# ---------------------------------------------------------------- visualize
class ChartSpec(BaseModel):
    analysis_index: int
    kind: Literal["bar", "barh", "line", "scatter", "hist"]
    x: str
    y: list[str] = []
    title: str
    takeaway: str = ""


class ChartPlan(BaseModel):
    charts: list[ChartSpec]


# ---------------------------------------------------------------- decide
class DecisionFinding(BaseModel):
    claim: str
    evidence: list[int] = []


class Recommendation(BaseModel):
    action: str
    why: str
    confidence: Literal["low", "medium", "high"] = "medium"
    evidence: list[int] = []


class Decision(BaseModel):
    headline: str
    summary: str = ""
    findings: list[DecisionFinding] = []
    recommendations: list[Recommendation] = []
    caveats: list[str] = []
    needs_human: list[str] = []
