"""Load and validate the evaluation datasets.

Both datasets are YAML so that multi-line reference SQL stays readable. The
loaders validate on read, because a typo in a case file should fail loudly
rather than quietly score as a pass.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

EVALUATION_DIR = Path(__file__).resolve().parent

ATTACK_CASES_PATH = EVALUATION_DIR / "attack_cases.yaml"
GOLDEN_CASES_PATH = EVALUATION_DIR / "golden_cases.yaml"

VALID_COMPARISONS = {"multiset", "ordered", "scalar", "none"}


@dataclass(frozen=True)
class AttackCase:
    """One adversarial query the guardrails must refuse."""

    case_id: str
    sql: str
    expected_status: str
    expected_stage: str
    description: str = ""


@dataclass(frozen=True)
class GoldenCase:
    """One question with a hand-written reference query to score against."""

    case_id: str
    question: str
    category: str
    reference_sql: str
    expected_tables: list[str] = field(default_factory=list)
    must_use_columns: list[str] = field(default_factory=list)
    comparison: str = "multiset"
    numeric_tolerance: float = 0.01
    max_repair_attempts: int = 2
    must_succeed: bool = True
    difficulty: str = "medium"
    notes: str = ""


def _read_yaml(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Evaluation dataset not found: {path}")

    loaded = yaml.safe_load(path.read_text()) or []

    if not isinstance(loaded, list):
        raise ValueError(
            f"{path.name} must contain a list of cases, got "
            f"{type(loaded).__name__}."
        )

    return loaded


def _require_unique_ids(case_ids: list[str], source: str) -> None:
    duplicates = {
        case_id
        for case_id in case_ids
        if case_ids.count(case_id) > 1
    }
    if duplicates:
        raise ValueError(
            f"Duplicate case_id(s) in {source}: {sorted(duplicates)}"
        )


def load_attack_cases(
    path: Path = ATTACK_CASES_PATH,
) -> list[AttackCase]:
    """Load the adversarial guardrail cases."""

    cases: list[AttackCase] = []

    for index, raw in enumerate(_read_yaml(path)):
        case_id = raw.get("case_id")
        if not case_id:
            raise ValueError(f"{path.name}[{index}] is missing case_id.")

        expect = raw.get("expect") or {}

        if "sql" not in raw:
            raise ValueError(f"{case_id}: missing sql.")
        if "status" not in expect or "stage" not in expect:
            raise ValueError(
                f"{case_id}: expect must declare both status and stage."
            )

        cases.append(
            AttackCase(
                case_id=case_id,
                sql=str(raw["sql"]),
                expected_status=str(expect["status"]),
                expected_stage=str(expect["stage"]),
                description=str(raw.get("description", "")),
            )
        )

    _require_unique_ids([case.case_id for case in cases], path.name)

    return cases


def load_golden_cases(
    path: Path = GOLDEN_CASES_PATH,
) -> list[GoldenCase]:
    """Load the question-answering cases."""

    cases: list[GoldenCase] = []

    for index, raw in enumerate(_read_yaml(path)):
        case_id = raw.get("case_id")
        if not case_id:
            raise ValueError(f"{path.name}[{index}] is missing case_id.")

        expect = raw.get("expect") or {}

        for required in ("question", "category"):
            if not raw.get(required):
                raise ValueError(f"{case_id}: missing {required}.")

        reference_sql = str(expect.get("reference_sql", "")).strip()
        comparison = str(expect.get("comparison", "multiset"))

        if comparison not in VALID_COMPARISONS:
            raise ValueError(
                f"{case_id}: comparison must be one of "
                f"{sorted(VALID_COMPARISONS)}, got {comparison!r}."
            )

        if comparison != "none" and not reference_sql:
            raise ValueError(
                f"{case_id}: comparison {comparison!r} needs a reference_sql."
            )

        cases.append(
            GoldenCase(
                case_id=case_id,
                question=str(raw["question"]),
                category=str(raw["category"]),
                reference_sql=reference_sql,
                expected_tables=list(expect.get("referenced_tables", [])),
                must_use_columns=list(expect.get("must_use_columns", [])),
                comparison=comparison,
                numeric_tolerance=float(
                    expect.get("numeric_tolerance", 0.01)
                ),
                max_repair_attempts=int(
                    expect.get("max_repair_attempts", 2)
                ),
                must_succeed=bool(expect.get("must_succeed", True)),
                difficulty=str(raw.get("difficulty", "medium")),
                notes=str(raw.get("notes", "")),
            )
        )

    _require_unique_ids([case.case_id for case in cases], path.name)

    return cases
