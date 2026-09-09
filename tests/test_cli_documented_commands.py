"""The eight commands the CLI review found documented but untested at the CLI layer.

Each calls a tested engine; none had a test that invoked the *command* — a renamed flag or a
broken import would have shipped. One CliRunner test each, the engine patched where it needs
git, gh, a model, a server, or a backend, run for real where it is deterministic and local.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ---- sdlc explain / workflows / workflow: the run and the profiles --------------------


def test_sdlc_explain_renders_a_case_and_names_a_missing_one(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.case import Case

    monkeypatch.setenv("SPINE_RUN_ARTIFACTS", str(tmp_path))
    from orchestrator.sdlc.autorun import default_artifacts_dir

    case = Case(run_id="run-1", issue_key="PROJ-1", title="Add refunds", profile="default")
    target = default_artifacts_dir("run-1") / "case.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    case.write(target)

    result = runner.invoke(app, ["sdlc", "explain", "run-1"])
    assert result.exit_code == 0, result.output
    assert "PROJ-1" in result.output or "run-1" in result.output
    as_json = runner.invoke(app, ["sdlc", "explain", "run-1", "--json"])
    assert as_json.exit_code == 0 and json.loads(as_json.output)["run_id"] == "run-1"

    missing = runner.invoke(app, ["sdlc", "explain", "run-9"])
    assert missing.exit_code == 2 and "No case for run 'run-9'" in missing.output


def test_sdlc_workflows_lists_the_shipped_profiles_and_a_repo_override_wins(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = runner.invoke(app, ["sdlc", "workflows", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "default" in result.output and "shipped" in result.output
    assert "any unmapped issue type" in result.output

    # A repo may carry its own profile of the same name; it is listed as the repo's.
    from orchestrator.sdlc.profiles import REPO_PROFILE_DIR, load_profile

    shipped = load_profile("default", tmp_path)
    repo_dir = tmp_path / REPO_PROFILE_DIR
    repo_dir.mkdir(parents=True)
    import yaml

    (repo_dir / "default.yaml").write_text(yaml.safe_dump(shipped.model_dump(mode="json")), encoding="utf-8")
    result = runner.invoke(app, ["sdlc", "workflows", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "repo" in result.output and "same name wins over shipped" in result.output


def test_sdlc_workflow_validates_and_prints_a_profile_and_names_an_unknown_one(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = runner.invoke(app, ["sdlc", "workflow", "default", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "✓ valid" in result.output and "next:" in result.output

    as_json = runner.invoke(app, ["sdlc", "workflow", "default", "--path", str(tmp_path), "--json"])
    assert as_json.exit_code == 0
    payload = json.loads(as_json.output)
    assert payload["valid"] is True and payload["ir"]["spec"]["nodes"]

    unknown = runner.invoke(app, ["sdlc", "workflow", "no-such-profile", "--path", str(tmp_path)])
    assert unknown.exit_code == 2 and "Available:" in unknown.output


# ---- mcp call / ingest-db: the client side, with a fake registry --------------------------


def test_mcp_call_invokes_one_tool_and_refuses_bad_arguments(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.mcp import MCPToolResult

    seen: dict[str, Any] = {}

    class _Registry:
        async def call(self, qualified: str, arguments: dict[str, Any]) -> MCPToolResult:
            seen["qualified"], seen["arguments"] = qualified, arguments
            if qualified == "db:forbidden":
                raise PermissionError("db:forbidden is not allow-listed")
            return MCPToolResult(text="42 rows", is_error=False)

    monkeypatch.setattr(
        "orchestrator.mcp.MCPRegistry.from_config", staticmethod(lambda config=None: _Registry())
    )

    result = runner.invoke(app, ["mcp", "call", "db:query", "--args", '{"sql": "select 1"}'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"tool": "db:query", "is_error": False, "text": "42 rows"}
    assert seen == {"qualified": "db:query", "arguments": {"sql": "select 1"}}

    assert runner.invoke(app, ["mcp", "call", "db:query", "--args", "not json"]).exit_code == 2
    assert runner.invoke(app, ["mcp", "call", "db:query", "--args", "[1, 2]"]).exit_code == 2
    denied = runner.invoke(app, ["mcp", "call", "db:forbidden"])
    assert denied.exit_code == 2 and "not allow-listed" in denied.output


def test_mcp_ingest_db_turns_an_introspected_schema_into_pkg_facts(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.pkg.schema import DBColumn, DBSchema, DBTable

    schema = DBSchema(
        database="shop",
        tables=(
            DBTable(
                name="orders",
                columns=(DBColumn(name="id", type="int"), DBColumn(name="total", type="numeric")),
            ),
        ),
    )
    seen: dict[str, Any] = {}

    async def fake_introspect(
        registry: Any, *, server: str, query_tool: str, sql_arg: str, db_schema: str
    ) -> DBSchema:
        seen.update(server=server, query_tool=query_tool, sql_arg=sql_arg, db_schema=db_schema)
        return schema

    monkeypatch.setattr(
        "orchestrator.mcp.MCPRegistry.from_config", staticmethod(lambda config=None: object())
    )
    monkeypatch.setattr("orchestrator.mcp.db.introspect_via_mcp", fake_introspect)

    result = runner.invoke(
        app, ["mcp", "ingest-db", "--server", "pg", "--query-tool", "run_sql", "--schema", "sales"]
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["database"] == "shop" and out["tables"] == {"orders": ["id", "total"]}
    assert sum(out["pkg_facts"].values()) > 0  # schema_to_facts ran for real: a table and its columns
    assert seen == {"server": "pg", "query_tool": "run_sql", "sql_arg": "sql", "db_schema": "sales"}


# ---- sdlc address-review / baseline / remediate: the back half ----------------------------


def test_sdlc_address_review_checks_out_then_responds_and_reports_each_failure(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.review_response import PRCheckoutError, ReviewResponse

    monkeypatch.chdir(tmp_path)  # no .env to bridge SDLC_REPO_URL in
    monkeypatch.delenv("SDLC_REPO_URL", raising=False)
    no_repo = runner.invoke(app, ["sdlc", "address-review", "--pr", "https://gh/o/r/pull/1"])
    assert no_repo.exit_code == 2 and "SDLC_REPO_URL" in no_repo.output

    seen: dict[str, Any] = {}

    async def checkout(repo_url: str, pr: str, **kw: Any) -> tuple[Path, str]:
        seen["clone"] = (repo_url, pr)
        return tmp_path, "feat/abc/PROJ-1"

    async def respond(deps: Any, **kw: Any) -> ReviewResponse:
        seen["respond"] = kw
        return ReviewResponse(comments=2, addressed=True, green=True, refines=1, detail="pushed")

    monkeypatch.setattr("orchestrator.sdlc.review_response.checkout_pr_worktree", checkout)
    monkeypatch.setattr("orchestrator.sdlc.review_response.respond_to_pr_feedback", respond)
    monkeypatch.setattr("orchestrator.sdlc.worker.build_deps", lambda: object())

    result = runner.invoke(
        app,
        [
            "sdlc",
            "address-review",
            "--pr",
            "https://gh/o/r/pull/1",
            "--repo",
            "https://gh/o/r.git",
            "--bot-login",
            "bot",
            "--max-refines",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output.split("\n", 1)[1])["addressed"] is True  # after the "Cloning …" line
    assert seen["clone"] == ("https://gh/o/r.git", "https://gh/o/r/pull/1")
    assert seen["respond"]["branch"] == "feat/abc/PROJ-1" and seen["respond"]["max_refines"] == 2

    async def failing_checkout(repo_url: str, pr: str, **kw: Any) -> tuple[Path, str]:
        raise PRCheckoutError("gh", "not logged in")

    monkeypatch.setattr("orchestrator.sdlc.review_response.checkout_pr_worktree", failing_checkout)
    failed = runner.invoke(app, ["sdlc", "address-review", "--pr", "p", "--repo", "u"])
    assert failed.exit_code == 1 and "not logged in" in failed.output


def test_sdlc_baseline_scores_the_gate_and_prints_json_or_a_report(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ledger.py").write_text(
        "class TokenLedger:\n    def record(self, stage, result):\n        return None\n", encoding="utf-8"
    )
    monkeypatch.setenv("SPINE_RUN_STATE", str(tmp_path / "state"))  # an empty run store

    as_json = runner.invoke(app, ["sdlc", "baseline", "--path", str(tmp_path), "--json"])
    assert as_json.exit_code == 0, as_json.output
    out = json.loads(as_json.output)
    assert out["gate"]["cases"] > 0 and 0.0 <= out["gate"]["accuracy"] <= 1.0
    assert out["runs"]["runs"] == 0
    assert set(out["gate"]) == {"accuracy", "cases", "false_refusals", "missed_refusals"}

    report = runner.invoke(app, ["sdlc", "baseline", "--path", str(tmp_path)])
    assert report.exit_code == 0 and "refus" in report.output.lower()


def test_sdlc_remediate_runs_each_material_finding_and_says_when_there_are_none(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"findings": []}), encoding="utf-8")
    mappings = tmp_path / "mappings.json"
    mappings.write_text("{}", encoding="utf-8")

    class _Store:
        def __init__(self, path: str) -> None:
            pass

        def load(self) -> dict[str, Any]:
            return {}

        def code_for_iri(self) -> dict[str, list[str]]:
            return {}

    monkeypatch.setattr("orchestrator.spine.MappingStore", _Store)
    monkeypatch.setattr("orchestrator.spine.infer_entity_iris", lambda report, mappings: {})

    outcomes: list[Any] = []

    async def execute(report: Any, *, runner: Any, **kw: Any) -> list[Any]:
        return outcomes

    monkeypatch.setattr("orchestrator.spine.execute_remediations", execute)

    none = runner.invoke(app, ["sdlc", "remediate", "--report", str(report), "--mappings", str(mappings)])
    assert none.exit_code == 0 and "nothing to remediate" in none.output

    outcomes.extend(
        [
            SimpleNamespace(
                entity_key="Order", title="Fix Order", ok=True, detail="branch left", result="feat/x"
            ),
            SimpleNamespace(
                entity_key="Invoice", title="Fix Invoice", ok=False, detail="tests red", result=None
            ),
        ]
    )
    some = runner.invoke(
        app,
        [
            "sdlc",
            "remediate",
            "--report",
            str(report),
            "--mappings",
            str(mappings),
            "--min-severity",
            "critical",
        ],
    )
    assert some.exit_code == 0, some.output
    assert "REMEDIATION: 2 task(s)" in some.output
    assert "[OK] Order: branch left → feat/x" in some.output and "[FAILED] Invoice: tests red" in some.output
