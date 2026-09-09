"""The review skill's documentation audit: the two checks that would have caught this week's
stale docs — dead links and mentions of removed surfaces — plus GitHub's slug rules."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "review-pr" / "scripts" / "docs_audit.py"
)


@pytest.fixture
def audit(tmp_path: Path) -> ModuleType:
    """The script as a module, pointed at a fixture tree (it is stdlib-only by design)."""
    spec = importlib.util.spec_from_file_location("docs_audit", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.set_root(tmp_path)
    return mod


def test_github_slug_matches_what_github_renders(audit: ModuleType) -> None:
    """Verified against github.com's rendering of USER_GUIDE.md: an em dash yields a double
    hyphen, punctuation vanishes, backticks and bold are stripped."""
    assert audit.github_slug("Step 1 — Install") == "step-1--install"
    assert (
        audit.github_slug("Step 7 — The full pipeline + web dashboard")
        == "step-7--the-full-pipeline--web-dashboard"
    )
    assert audit.github_slug("What's new") == "whats-new"
    assert audit.github_slug("`doctor` says **which** install") == "doctor-says-which-install"
    assert audit.github_slug("Asking across several repositories") == "asking-across-several-repositories"


def test_heading_anchors_number_duplicates_and_skip_fences(audit: ModuleType) -> None:
    text = "# Title\n\n## Notes\n\n```\n## not a heading\n```\n\n## Notes\n"
    assert audit.heading_anchors(text) == {"title", "notes", "notes-1"}


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def test_check_links_reports_missing_files_and_anchors_but_not_urls_or_fenced_links(
    audit: ModuleType, tmp_path: Path
) -> None:
    _tree(
        tmp_path,
        {
            "README.md": "\n".join(
                [
                    "# Spine",
                    "See [the guide](USER_GUIDE.md#step-1--install) and [ops](OPERATIONS.md).",
                    "Broken: [gone](PROGRESS.md) and [bad anchor](USER_GUIDE.md#step-1-install).",
                    "External: [site](https://example.com/x#frag) and [mail](mailto:a@b.c).",
                    "In code: `[not a link](nope.md)`.",
                    "```",
                    "[fenced](also-nope.md)",
                    "```",
                    "Same file: [top](#spine) and [nowhere](#missing).",
                    "![diagram](assets/arch.svg)",
                ]
            ),
            "USER_GUIDE.md": "# Guide\n\n## Step 1 — Install\n\ntext\n",
            "OPERATIONS.md": "# Ops\n",
            "assets/arch.svg": "<svg/>",
        },
    )
    findings = audit.check_links()
    assert any("README.md:3 → PROGRESS.md — file not found" in f for f in findings)
    assert any(
        "README.md:3 → USER_GUIDE.md#step-1-install — no such anchor (nearest: step-1--install)" in f
        for f in findings
    )
    assert any("README.md:9 → #missing — no such anchor" in f for f in findings)
    assert len(findings) == 3, findings  # the good link, the URLs, the fenced/inline ones, the image: silent


def test_check_removed_flags_live_mentions_and_reads_stamped_paragraphs_as_history(
    audit: ModuleType, tmp_path: Path
) -> None:
    _tree(
        tmp_path,
        {
            "README.md": "\n".join(
                [
                    "# Spine",
                    "",
                    "A **CLI**, a **web dashboard**, a **terminal UI**, and **MCP**.",
                    "",
                    "**3.32.0 (current)** — the plugin speaks the whole protocol. The operator tools",
                    "are the terminal UI's successor — the TUI is gone.",
                    "",
                    "Use the web UI (or terminal UI) to approve gates. Intuition is not a TUI.",
                ]
            ),
        },
    )
    findings = audit.check_removed({"terminal UI": "surface", "TUI": "surface"})
    stale = [f for f in findings if f.startswith("[STALE]")]
    info = [f for f in findings if f.startswith("[INFO]")]
    assert [f.split(": README.md:")[1][:1] for f in stale] == [
        "3",
        "8",
        "8",
    ]  # line 3; line 8 twice (both terms)
    assert all(":6:" in f for f in info) and len(info) == 2  # the stamped paragraph: history
    assert not any(
        "Intuition" in f and "'TUI'" in f and f.startswith("[STALE]") and ":3:" in f for f in findings
    )


def test_history_lines_cover_the_whole_stamped_paragraph(audit: ModuleType) -> None:
    text = (
        "**3.31.0** — first line\nsecond line of the same paragraph\n\nnot history\n2026-09-04 dated line\n"
    )
    assert audit.history_lines(text) == {1, 2, 5}
    # Prose that pins a version is history too: a status row saying what shipped and when.
    text = (
        "| unified-ui | P4 TUI shipped 1.18.0, removed in 3.31.0 |\n"
        "the TUI is gone since 3.31.0\n\nthe TUI is a surface\n"
    )
    assert audit.history_lines(text) == {1, 2}


def test_registry_parsers_read_what_the_diff_check_compares(audit: ModuleType) -> None:
    assert audit.cli_commands_in('@app.command("tui", rich_help_panel=X)\n@sdlc_app.command("run")\n') == {
        "tui",
        "run",
    }
    assert audit.mcp_tools_in(
        'x\nOUTPUTS: dict[str, type] = {\n    "doctor": DoctorOut,\n    "map_repo": MapRepoOut,\n}\n'
    ) == {
        "doctor",
        "map_repo",
    }
    py = '[project.optional-dependencies]\ntui = [\n  "textual",\n]\ndev = [\n]\nall = [\n]\n[tool.x]\n'
    assert audit.extras_in(py) == {"tui"}


def test_the_matrix_names_the_removed_surface_trigger() -> None:
    matrix = (_SCRIPT.parents[1] / "docs-matrix.md").read_text(encoding="utf-8")
    assert "Removed feature" in matrix and "--removed" in matrix
