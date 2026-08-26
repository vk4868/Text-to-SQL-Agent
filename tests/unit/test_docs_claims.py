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


class TestFrontEndsImportCleanly:
    """The demo and the web app are the things a reader will run first."""

    def test_demo_script_imports_without_credentials(self):
        """It must not build a live client at import time."""

        import subprocess
        import sys

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util, sys;"
                "from unittest.mock import patch;"
                "p=patch('google.cloud.bigquery.Client');p.start();"
                "spec=importlib.util.spec_from_file_location("
                "'demo','scripts/demo.py');"
                "m=importlib.util.module_from_spec(spec);"
                "spec.loader.exec_module(m);"
                "print('QUESTIONS', len(m.QUESTIONS))",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert completed.returncode == 0, completed.stderr
        assert "QUESTIONS" in completed.stdout

    def test_streamlit_app_exists_and_parses(self):
        import ast

        app = REPO_ROOT / "app.py"

        assert app.exists()
        # Syntax errors here would only surface when someone runs the demo.
        ast.parse(app.read_text())

    def test_streamlit_app_uses_the_agent_not_its_own_logic(self):
        """Presentation only: no graph or pipeline construction in the UI."""

        source = (REPO_ROOT / "app.py").read_text()

        assert "InsightsAgent" in source
        assert "build_insights_graph" not in source
        assert "SQLExecutionPipeline" not in source


class TestDocumentedNumbersMatchTheEvidence:
    """The docs quote figures; those figures must come from the report.

    An audit found the test count wrong in five places and the latency share
    stale. Numbers drift silently — these tests make them fail loudly.
    """

    @staticmethod
    def latest_report() -> dict:
        import json

        path = REPO_ROOT / "evaluation" / "reports" / "latest.json"

        if not path.exists():
            pytest.skip("no evaluation report yet; run the evaluation")

        return json.loads(path.read_text())

    def test_the_report_is_committed_for_readers(self):
        """PORTFOLIO tells readers to check it, so it must be in the repo."""

        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "evaluation/reports/latest.json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert tracked, (
            "docs/PORTFOLIO.md invites the reader to check "
            "evaluation/reports/latest.json, so it must be committed"
        )

    def test_readme_pass_rate_matches_the_report(self):
        summary = self.latest_report()["summary"]
        readme = (REPO_ROOT / "README.md").read_text()

        expected = f"{summary['passed']}/{summary['cases']}"

        assert expected in readme, (
            f"README should quote the measured pass rate {expected}"
        )

    def test_readme_metric_counts_match_the_report(self):
        metrics = self.latest_report()["summary"]["metrics"]
        readme = (REPO_ROOT / "README.md").read_text()

        for name in ("execution_accuracy", "analysis_grounding"):
            entry = metrics[name]
            quoted = f"{entry['passed']}/{entry['total']}"
            assert quoted in readme, (
                f"README quotes {name} but not as the measured {quoted}"
            )

    def test_guardrail_count_matches_the_case_file(self):
        from evaluation.cases import load_attack_cases

        total = len(load_attack_cases())

        for doc in (REPO_ROOT / "README.md", REPO_ROOT / "docs" / "PORTFOLIO.md"):
            text = doc.read_text()
            assert f"{total}/{total}" in text, (
                f"{doc.name} should quote {total}/{total} adversarial "
                f"queries, matching attack_cases.yaml"
            )

    @pytest.mark.parametrize(
        "doc",
        [
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "PORTFOLIO.md",
            REPO_ROOT / "CLAUDE.md",
        ],
        ids=doc_ids,
    )
    def test_the_documented_test_floor_still_holds(self, doc):
        """The docs quote a floor ("400+"), not an exact count.

        An exact number goes stale the moment anyone adds a test — an audit
        found it wrong in five places at once. A floor stays true as the
        suite grows and fails loudly if it ever shrinks below the claim.
        """

        import re

        text = doc.read_text()

        floors = {
            int(match)
            for match in re.findall(
                r"\b(\d{3})\+\s*(?:hermetic\s+)?tests", text
            )
        }
        exact = {
            int(match)
            for match in re.findall(
                r"\b(\d{3})\s+(?:hermetic\s+)?tests\b", text
            )
        }

        if not floors and not exact:
            return

        actual = self.collected_test_count()

        assert not exact, (
            f"{doc.name} quotes an exact test count {sorted(exact)}; use a "
            f'floor like "{actual // 100 * 100}+ tests" so it does not go '
            "stale"
        )

        for floor in floors:
            assert actual >= floor, (
                f"{doc.name} claims {floor}+ tests but the suite has only "
                f"{actual}"
            )

    @staticmethod
    def collected_test_count() -> int:
        import subprocess
        import sys

        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )

        import re

        # `--collect-only -q` prints "path/to/test_x.py: N" per file and no
        # grand total, so sum them.
        counts = [
            int(match)
            for match in re.findall(r"^\S+\.py: (\d+)$", completed.stdout, re.MULTILINE)
        ]

        if not counts:
            pytest.skip("could not determine the collected test count")

        return sum(counts)


class TestGuardrailClaimIsPrecise:
    """An overstated safety claim is the worst kind of documentation error.

    Stage 2 makes a `list_tables` catalog call to build the allowlist, so
    "nothing reached BigQuery" was false. The defensible claim is that no
    table data is scanned: no dry run, no execution, nothing billed.
    """

    OVERSTATEMENTS = [
        "nothing reached BigQuery",
        "nothing reaches BigQuery",
        "before anything reaches BigQuery",
    ]

    @pytest.mark.parametrize("doc", DOCS, ids=doc_ids)
    def test_docs_do_not_overstate_what_the_guardrails_avoid(self, doc):
        text = doc.read_text()

        found = [
            phrase for phrase in self.OVERSTATEMENTS if phrase in text
        ]

        assert not found, (
            f"{doc.name} overstates the guardrail claim: {found}. Stage 2 "
            "makes a catalog call; say 'before a byte of table data is "
            "scanned' instead."
        )

    def test_demo_script_does_not_overstate_it_either(self):
        source = (REPO_ROOT / "scripts" / "demo.py").read_text()

        found = [
            phrase for phrase in self.OVERSTATEMENTS if phrase in source
        ]

        assert not found, f"scripts/demo.py overstates it: {found}"

    def test_the_guardrails_really_do_avoid_query_execution(self):
        """The claim that IS made must hold."""

        from src.sql_execution_pipeline import SQLExecutionPipeline
        from tests.fakes.bigquery import FakeBigQueryService

        service = FakeBigQueryService(
            project_id="your-project-id",
            dataset_id="business_insights",
        )
        pipeline = SQLExecutionPipeline(bigquery_service=service)

        from evaluation.cases import load_attack_cases

        for case in load_attack_cases():
            pipeline.execute(case.sql)

        assert service.dry_run_calls == []
        assert service.run_query_calls == []
