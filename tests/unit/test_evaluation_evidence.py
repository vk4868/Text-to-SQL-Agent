"""Evidence extraction: keep only what the report scored, safely."""

import json

from evaluation.evidence import extract_evidence, main
from evaluation.cases import PROJECT_PLACEHOLDER

REAL = "acme-real-123"


def graph_run(run_id, sql_ids, **extra):
    return {
        "event": "graph_run",
        "graph_run_id": run_id,
        "question": "q",
        "sql": {"executed_sql": f"SELECT 1 FROM `{REAL}.d.t`"},
        "runtime": {"sql_execution_run_ids": sql_ids},
        **extra,
    }


def sql_exec(run_id):
    return {"event": "sql_execution", "run_id": run_id, "status": "success"}


def report():
    return {
        "cases": [
            {"case_id": "a", "trial": 1, "graph_run_id": "g-a1"},
            {"case_id": "a", "trial": 2, "graph_run_id": "g-a2"},
            {"case_id": "b", "trial": 1, "graph_run_id": "g-b1"},
        ]
    }


def records():
    return [
        sql_exec("attack-1"),
        graph_run("stray", ["s-stray"]),
        sql_exec("s-stray"),
        sql_exec("s-b1"),
        graph_run("g-b1", ["s-b1"]),
        graph_run("g-a1", ["s-a1", "s-a1b"]),
        sql_exec("s-a1"),
        sql_exec("s-a1b"),
        graph_run("g-a2", ["s-a2"], rows=[{"x": 1}]),
        sql_exec("s-a2"),
    ]


class TestExtractEvidence:
    def test_kept_set_order_and_tags(self):
        kept, summary = extract_evidence(report(), records(), REAL)

        order = [
            r.get("graph_run_id") or r.get("run_id") for r in kept
        ]
        assert order == [
            "g-a1", "s-a1", "s-a1b", "g-a2", "s-a2", "g-b1", "s-b1",
        ]
        first = kept[0]
        assert (first["case_id"], first["trial"]) == ("a", 1)
        assert first["evidence_version"] == 1
        assert summary["graph_runs"] == 3
        assert summary["sql_executions"] == 4
        assert summary["missing_graph_run_ids"] == []

    def test_project_id_redacted(self):
        kept, _ = extract_evidence(report(), records(), REAL)
        assert REAL not in json.dumps(kept)
        assert "your-project-id" in json.dumps(kept)

    def test_missing_ids_are_reported(self):
        rep = report()
        rep["cases"].append(
            {"case_id": "c", "trial": 1, "graph_run_id": "ghost"}
        )
        _, summary = extract_evidence(rep, records(), REAL)
        assert summary["missing_graph_run_ids"] == ["ghost"]

    def test_planted_forbidden_keys_are_stripped(self):
        recs = records()
        recs[5]["analysis"] = {"raw_model_output": "x", "ok": 1}
        recs[5]["nested"] = [{"prompt": "p"}]
        kept, summary = extract_evidence(report(), recs, REAL)

        text = json.dumps(kept)
        for key in ('"rows"', '"raw_model_output"', '"prompt"'):
            assert key not in text
        assert summary["stripped_keys"] == 3  # rows + raw_model_output + prompt
        assert any(r.get("analysis") == {"ok": 1} for r in kept)


class TestCli:
    def test_main_writes_jsonl(self, tmp_path, capsys):
        runs = tmp_path / "runs.jsonl"
        runs.write_text(
            "\n".join(json.dumps(r) for r in records()) + "\nnot json\n"
        )
        rep = tmp_path / "rep.json"
        rep.write_text(json.dumps(report()))
        out = tmp_path / "out" / "evidence.jsonl"

        code = main(
            ["--runs", str(runs), "--report", str(rep),
             "--out", str(out), "--project-id", REAL]
        )

        assert code == 0
        lines = [json.loads(x) for x in out.read_text().splitlines()]
        assert len(lines) == 7
        err = capsys.readouterr().err
        assert "graph_run kept: 3" in err
        assert "sql_execution kept: 4" in err

    def test_main_exits_one_when_ids_missing(self, tmp_path, capsys):
        runs = tmp_path / "runs.jsonl"
        runs.write_text(json.dumps(graph_run("g-a1", [])) + "\n")
        rep = tmp_path / "rep.json"
        rep.write_text(json.dumps(report()))

        code = main(
            ["--runs", str(runs), "--report", str(rep),
             "--out", str(tmp_path / "o.jsonl"), "--project-id", REAL]
        )

        assert code == 1
        assert "g-a2" in capsys.readouterr().err


class TestRedactionGuards:
    def _files(self, tmp_path):
        runs = tmp_path / "runs.jsonl"
        runs.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        rep = tmp_path / "rep.json"
        rep.write_text(json.dumps(report()))
        return runs, rep, tmp_path / "out.jsonl"

    def test_placeholder_project_id_is_refused(self, tmp_path, capsys):
        runs, rep, out = self._files(tmp_path)

        code = main(
            ["--runs", str(runs), "--report", str(rep), "--out", str(out),
             "--project-id", PROJECT_PLACEHOLDER]
        )

        assert code == 2
        assert not out.exists()
        assert "--allow-placeholder" in capsys.readouterr().err

    def test_placeholder_allowed_with_flag(self, tmp_path):
        runs, rep, out = self._files(tmp_path)

        code = main(
            ["--runs", str(runs), "--report", str(rep), "--out", str(out),
             "--project-id", PROJECT_PLACEHOLDER, "--allow-placeholder"]
        )

        assert code == 0
        assert out.exists()

    def test_leaked_id_after_redaction_refuses_to_write(
        self, tmp_path, capsys
    ):
        runs, rep, out = self._files(tmp_path)
        leaky = records()
        # The id as a dict key is never rewritten by the redactor.
        leaky[5]["extra"] = {REAL: 1}
        runs.write_text("\n".join(json.dumps(r) for r in leaky) + "\n")

        code = main(
            ["--runs", str(runs), "--report", str(rep), "--out", str(out),
             "--project-id", REAL]
        )

        assert code == 2
        assert not out.exists()
        assert "project id" in capsys.readouterr().err
