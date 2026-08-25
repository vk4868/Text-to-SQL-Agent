"""Keep the documentation from going stale.

README.md and PIPELINE.md previously described a component that had been
deleted and test scripts that no longer existed, because nothing checked
them. These tests assert that every file path and every command the docs
mention actually exists.

They cannot check that the prose is *true*, only that it is not obviously
false. That is still the difference between docs that rot in a month and
docs that fail loudly the moment something moves.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "PIPELINE.md",
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "docs" / "PORTFOLIO.md",
    REPO_ROOT / "docs" / "INTERVIEW.md",
]

#: `src/foo.py`, `scripts/smoke/`, `evaluation/scorers.py`
PATH_PATTERN = re.compile(
    r"`((?:src|tests|scripts|evaluation|Datasets|docs)/[A-Za-z0-9_./*-]+)`"
)

#: A python module invoked as `python -m package.module`
MODULE_PATTERN = re.compile(r"python -m ([a-z_]+(?:\.[a-z_]+)+)")

#: Paths that are illustrative rather than real.
IGNORED_PATHS = {
    "src/foo.py",
}


def is_glob(path: str) -> bool:
    """A pattern like scripts/smoke/smoke_*.py names a family, not a file."""

    return any(char in path for char in "*?[")


def doc_ids(path: Path) -> str:
    return path.name


@pytest.mark.parametrize("doc", DOCS, ids=doc_ids)
class TestDocumentedPathsExist:
    def test_doc_is_present(self, doc):
        assert doc.exists(), f"{doc.name} is referenced but missing"

    def test_every_referenced_path_exists(self, doc):
        referenced = set(PATH_PATTERN.findall(doc.read_text()))

        missing = sorted(
            path
            for path in referenced - IGNORED_PATHS
            if not is_glob(path)
            and not (REPO_ROOT / path.rstrip("/")).exists()
        )

        assert not missing, (
            f"{doc.name} references paths that do not exist: {missing}"
        )

    def test_every_documented_module_is_importable(self, doc):
        modules = set(MODULE_PATTERN.findall(doc.read_text()))

        missing = []
        for module in modules:
            parts = module.split(".")
            candidate = REPO_ROOT.joinpath(*parts)
            if not (
                candidate.with_suffix(".py").exists()
                or (candidate / "__init__.py").exists()
            ):
                missing.append(module)

        assert not missing, (
            f"{doc.name} documents `python -m` for modules that do not "
            f"exist: {sorted(missing)}"
        )


class TestDocumentedEntryPointsExist:
    """The commands a reader will actually type."""

    def test_main_entry_point(self):
        assert (REPO_ROOT / "main.py").exists()

    def test_evaluation_entry_point(self):
        assert (
            REPO_ROOT / "evaluation" / "run_evaluation.py"
        ).exists()

    def test_run_report_script(self):
        assert (REPO_ROOT / "scripts" / "report_runs.py").exists()

    def test_demo_script(self):
        assert (REPO_ROOT / "scripts" / "demo.py").exists()

    def test_smoke_scripts_are_not_collectable_by_pytest(self):
        """They are print-only scripts and several need live services."""

        smoke = list((REPO_ROOT / "scripts" / "smoke").glob("*.py"))

        assert smoke, "expected smoke scripts to exist"
        assert not any(
            path.name.startswith("test_") for path in smoke
        ), "a smoke script is named test_*, so pytest would collect it"


class TestNoReferencesToDeletedComponents:
    """The specific way these docs went stale last time."""

    DELETED = [
        "QuestionToSQLPipeline",
        "QuestionToInsightsPipeline",
        "question_to_sql_pipeline",
        "question_to_insights_pipeline",
    ]

    #: Words that mark a mention as historical rather than current.
    RETIREMENT_WORDS = (
        "retired",
        "deleted",
        "removed",
        "no longer",
        "git history",
        "previously",
        "used to",
        "were replaced",
    )

    @pytest.mark.parametrize("doc", DOCS, ids=doc_ids)
    def test_docs_do_not_describe_deleted_classes(self, doc):
        """A doc may say a component WAS retired; it may not describe it
        as if it still exists."""

        offenders = []

        for line in doc.read_text().splitlines():
            lowered = line.lower()

            if any(word in lowered for word in self.RETIREMENT_WORDS):
                continue

            offenders.extend(
                f"{name} (line: {line.strip()[:60]})"
                for name in self.DELETED
                if f"`{name}`" in line
            )

        assert not offenders, (
            f"{doc.name} describes deleted components as current: "
            f"{offenders}"
        )

    def test_source_tree_has_no_deleted_modules(self):
        for name in ("question_to_sql_pipeline", "question_to_insights_pipeline"):
            assert not (REPO_ROOT / "src" / f"{name}.py").exists()


class TestConfigSettingsAreReal:
    """The README documents a settings table; the settings must exist."""

    DOCUMENTED = [
        "MAX_QUERY_BYTES",
        "MAX_RESULT_ROWS",
        "QUERY_TIMEOUT_SECONDS",
        "MAX_SQL_REPAIR_ATTEMPTS",
        "MAX_ANALYSIS_ROWS",
        "SCHEMA_CACHE_TTL_SECONDS",
    ]

    @pytest.mark.parametrize("setting", DOCUMENTED)
    def test_setting_exists(self, setting):
        from src import config

        assert hasattr(config, setting), (
            f"README documents {setting} but src/config.py has no such "
            "setting"
        )

    def test_readme_mentions_every_safety_cap(self):
        readme = (REPO_ROOT / "README.md").read_text()

        for setting in self.DOCUMENTED:
            assert setting in readme
