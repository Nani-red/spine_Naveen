"""The reusable SDLC workflow — the governed build path as a pipeline.

Like `spine-comprehension.yml`, this file is a **published interface**: other repositories
reference it by tag. These tests pin the properties that make it safe to hand to a stranger
and honest about where a human decides. The design is in the workflow's own header.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
WORKFLOW = WORKFLOWS / "spine-sdlc.yml"
DOGFOOD = WORKFLOWS / "sdlc-dogfood.yml"


def _load(path: Path = WORKFLOW) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(data: dict[Any, Any]) -> dict[str, Any]:
    """PyYAML reads a bare ``on:`` key as the boolean ``True`` — YAML 1.1, not a typo."""
    triggers = data.get("on", data.get(True))
    assert isinstance(triggers, dict), "no trigger block"
    return triggers


def _steps(job: str) -> list[dict[str, Any]]:
    steps = _load()["jobs"][job]["steps"]
    assert isinstance(steps, list) and steps, f"job {job!r} has no steps"
    return [dict(step) for step in steps]


def test_it_is_callable_by_another_repository() -> None:
    assert "workflow_call" in _triggers(_load())


def test_it_is_two_jobs_split_where_the_model_starts() -> None:
    """`plan` is the deterministic half; `build` waits on it and on a human."""
    jobs = _load()["jobs"]
    assert set(jobs) == {"plan", "build"}
    assert jobs["build"]["needs"] == "plan"


def test_the_plan_job_needs_no_model_key() -> None:
    """The plan job's promise: intake → investigate → validity → design with nothing spent.

    It may read `CHECKOUT_TOKEN` to clone the sibling repositories a multi-repo graph declares,
    and nothing else. A plan job that touched the model key would be one that could spend.
    """
    text = yaml.safe_dump(_load()["jobs"]["plan"])
    assert "ANTHROPIC_API_KEY" not in text


def test_no_secret_is_required() -> None:
    """Plan-only callers pass nothing. The build job checks for its key at run time and says so."""
    secrets = _triggers(_load())["workflow_call"]["secrets"]
    assert secrets, "the build job needs a model key — it must be declarable"
    assert all(spec.get("required") is False for spec in secrets.values()), secrets


def test_the_human_gate_is_a_github_environment() -> None:
    """Required reviewers on the environment are the plan gate; the job records their decision.

    The environment name is an input so a caller can bind the job to its own reviewers, and
    the approval is written with `sdlc approve` so `sdlc autorun --plan-gate` — the default —
    has something to check rather than being switched off.
    """
    build = _load()["jobs"]["build"]
    assert build["environment"] == "${{ inputs.environment }}"
    scripts = "\n".join(step.get("run") or "" for step in build["steps"])
    assert "sdlc approve" in scripts
    assert "--no-plan-gate" not in scripts, (
        "the gate must not be bypassed by the job that exists to honour it"
    )


def test_permissions_are_least_privilege_per_job() -> None:
    data = _load()
    assert data["permissions"] == {"contents": "read"}
    assert data["jobs"]["plan"]["permissions"] == {"contents": "read"}
    assert data["jobs"]["build"]["permissions"] == {"contents": "write", "pull-requests": "write"}


def test_no_expression_reaches_a_shell_inline() -> None:
    """Script injection, the failure mode of reusable workflows generally.

    Inputs and event data are caller- or attacker-controlled. Every value a `run:` block needs
    is passed through `env:` and quoted, so nothing GitHub substitutes before `bash` sees it
    can execute. Stricter than the comprehension workflow's rule (no `github.event`): here no
    `${{` at all inside a script, because `inputs.spec` is as attacker-shaped as a PR title.
    """
    for job in ("plan", "build"):
        for step in _steps(job):
            script = step.get("run") or ""
            assert "${{" not in script, (
                f"{job}: step {step.get('name')!r} interpolates an expression into a shell block; "
                "pass it via env: and quote the variable"
            )


def test_delivery_is_refused_where_the_pipeline_would_build_at_the_wrong_level() -> None:
    """Delivery into a subdirectory is unsupported (single package root); refuse, do not scaffold.

    The refusal is the build job's first step, before a checkout or an install, so the human
    who approved the environment is told immediately rather than after a model ran.
    """
    first = _steps("build")[0]
    script = first.get("run") or ""
    assert "TARGET_DIR" in script and "exit 1" in script
    assert "sdlc-target-layout-scaffold" in script, (
        "the refusal must point at the design record that explains it"
    )


def test_submodules_are_checked_out_in_both_jobs() -> None:
    """A superproject's submodules are part of what is read and what is built."""
    for job in ("plan", "build"):
        checkouts = [s for s in _steps(job) if str(s.get("uses", "")).startswith("actions/checkout")]
        assert checkouts, f"{job}: no checkout"
        assert all(c["with"].get("submodules") == "recursive" for c in checkouts), job


def test_the_multi_repo_guard_runs_before_anything_reads_the_graph() -> None:
    """An un-initialised submodule is an empty directory that loads without error."""
    names = [s.get("name") or s.get("uses") for s in _steps("plan")]
    guard = next(i for i, n in enumerate(names) if "Verify every declared repository" in str(n))
    brief = next(i for i, n in enumerate(names) if "across repositories" in str(n))
    plan = next(i for i, n in enumerate(names) if "Build document" in str(n))
    assert guard < brief < plan, names


def test_this_repository_runs_the_workflow_on_its_own_checkout() -> None:
    """A workflow nothing runs is a claim nobody re-checks. Dogfood uses the local file."""
    data = _load(DOGFOOD)
    job = data["jobs"]["spine"]
    assert job["uses"] == "./.github/workflows/spine-sdlc.yml"
    assert job["with"]["install-from"].startswith("./repo"), (
        "must install the checkout under test, not a release"
    )
    assert "pull_request" in _triggers(data)
    assert "workflow_dispatch" in _triggers(data)
