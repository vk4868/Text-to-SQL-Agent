"""Project-id substitution, redaction and report-name writing."""

import dataclasses

import pytest

from evaluation.cases import (
    PROJECT_PLACEHOLDER,
    load_attack_cases,
    load_golden_cases,
)
from evaluation.report import redact_project_id
from evaluation.run_evaluation import build_parser, write_reports

REAL = "real-proj"


class TestLoaders:
    def test_golden_cases_substitute_project(self):
        cases = load_golden_cases(project_id=REAL)
        assert cases
        for case in cases:
            assert PROJECT_PLACEHOLDER not in case.reference_sql
            assert PROJECT_PLACEHOLDER not in "".join(case.expected_tables)
            if case.reference_sql:
                assert f"{REAL}.business_insights" in case.reference_sql
            for table in case.expected_tables:
                assert table.startswith(f"{REAL}.business_insights")

    def test_attack_cases_substitute_but_keep_foreign(self):
        cases = {c.case_id: c for c in load_attack_cases(project_id=REAL)}
        assert "bigquery-public-data.samples.natality" in (
            cases["foreign_project"].sql
        )
        for case in cases.values():
            assert PROJECT_PLACEHOLDER not in case.sql

    def test_placeholder_is_a_no_op(self):
        """Loading under the placeholder leaves the YAML text untouched."""

        golden = load_golden_cases(project_id=PROJECT_PLACEHOLDER)
        attack = load_attack_cases(project_id=PROJECT_PLACEHOLDER)
        for case in golden:
            if case.reference_sql:
                assert f"{PROJECT_PLACEHOLDER}.business_insights" in (
                    case.reference_sql
                )
            for table in case.expected_tables:
                assert table.startswith(PROJECT_PLACEHOLDER)
        assert sum(PROJECT_PLACEHOLDER in c.sql for c in attack) >= 10
        assert [dataclasses.asdict(c) for c in golden]

    def test_default_resolves_config_at_call_time(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config, "PROJECT_ID", REAL)
        cases = load_attack_cases()
        assert any(f"{REAL}.business_insights" in c.sql for c in cases)


class TestRedaction:
    def test_nested_redaction(self):
        value = {
            "a": [f"SELECT * FROM `{REAL}.x.y`", {"k": (f"{REAL}",)}],
            "n": 1,
            "f": 1.5,
            "none": None,
            "b": True,
        }
        out = redact_project_id(value, REAL)
        assert REAL not in repr(out)
        assert out["a"][0] == f"SELECT * FROM `{PROJECT_PLACEHOLDER}.x.y`"
        assert out["a"][1]["k"] == (PROJECT_PLACEHOLDER,)
        assert (out["n"], out["f"], out["none"], out["b"]) == (
            1, 1.5, None, True,
        )

    def test_noop_cases(self):
        value = {"a": "your-project-id.x"}
        assert redact_project_id(value, PROJECT_PLACEHOLDER) is value
        assert redact_project_id(value, "") is value

    def test_round_trip_symmetry(self):
        for case in load_golden_cases(project_id=REAL):
            assert PROJECT_PLACEHOLDER in redact_project_id(
                case.reference_sql, REAL
            ) or not case.reference_sql


class TestWriteReports:
    def test_report_name_writes_only_named_files(self, tmp_path):
        written = write_reports(
            "md", {"a": 1}, reports_dir=tmp_path / "reports",
            timestamp="t", report_name="before_semantic_gate",
        )
        names = sorted(p.name for p in (tmp_path / "reports").iterdir())
        assert names == [
            "before_semantic_gate.json", "before_semantic_gate.md",
        ]
        assert len(written) == 2

    def test_default_writes_latest_and_stamped(self, tmp_path):
        write_reports(
            "md", {"a": 1}, reports_dir=tmp_path / "reports",
            timestamp="2026-01-01T00:00:00.1",
        )
        names = sorted(p.name for p in (tmp_path / "reports").iterdir())
        assert names == [
            "latest.json",
            "latest.md",
            "report-2026-01-01T00-00-00-1.json",
            "report-2026-01-01T00-00-00-1.md",
        ]

    def test_parser_accepts_report_name(self):
        args = build_parser().parse_args(["--report-name", "x"])
        assert args.report_name == "x"
        assert build_parser().parse_args([]).report_name is None


class TestRedactionScope:
    def test_dict_keys_are_never_rewritten(self):
        out = redact_project_id({REAL: f"{REAL}.x"}, REAL)
        assert list(out) == [REAL]
        assert out[REAL] == f"{PROJECT_PLACEHOLDER}.x"

    def test_embedded_in_a_longer_identifier_is_left_alone(self):
        text = f"prefix-{REAL} {REAL}-suffix {REAL}_x x{REAL} `{REAL}.d`"
        out = redact_project_id(text, REAL)
        assert out == (
            f"prefix-{REAL} {REAL}-suffix {REAL}_x x{REAL} "
            f"`{PROJECT_PLACEHOLDER}.d`"
        )


class TestReportNameValidation:
    def test_bad_names_rejected(self):
        for bad in ("latest", "../x", "a/b", "a\\b", "report-1", "x..y"):
            with pytest.raises(SystemExit):
                build_parser().parse_args(["--report-name", bad])

    def test_good_name_accepted(self):
        args = build_parser().parse_args(["--report-name", "baseline_1"])
        assert args.report_name == "baseline_1"
