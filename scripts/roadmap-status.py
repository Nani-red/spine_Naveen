#!/usr/bin/env python3
"""The roadmap-currency gate (§8.1 of `docs/specs/templates/language-track.md`).

Every language-track roadmap under `docs/specs/` carries a "living table" — one row per
phase, with **Status · Started · Finished · Evidence** columns updated in the same commit
as the work. Each roadmap states its own rule ("a phase is DONE only when its evidence
column links a commit, a test name, or a pasted command result") but nothing checked it.
This does.

    python scripts/roadmap-status.py            # print every phase table found
    python scripts/roadmap-status.py --check     # non-zero if any check fails

Checks, each narrow enough to avoid the failure mode below:

1. **DONE needs receipts.** A row whose Status is DONE (checkmark) must have a non-empty
   Started, Finished, and Evidence cell.
2. **A cross-spec dependency isn't jumped.** A roadmap whose header names
   `**Depends on:** [other.md](other.md) merged (P<n> for ...)` may not have any phase
   Started until `other.md`'s own table shows P<n> as DONE. `perl-codegen-roadmap.md`'s
   dependency on `perl-support-roadmap.md`'s P2 is the first case this enforces.
3. **A roadmap's own header doesn't contradict its own table.** If the phase table already
   has Evidence in it, the document's top `**Status:**` line can no longer say "no code
   written" or "plan for review" — the two halves of the same file disagreeing is a defect
   nothing but reading both at once catches.
4. **Every roadmap with a phase table is indexed.** `SPEC-INDEX.md` must link to it.
5. **Relative links inside a tracked roadmap resolve**, the same rule `docs_audit.py`
   applies to the user-facing docs — roadmaps live outside that script's `USER_DOCS` list,
   so nothing was checking them. (Found live: `perl-support-roadmap.md` linked
   `kotlin-support-roadmap.md` twice; that file does not exist in this checkout.)

**What this deliberately does not attempt:** classifying whether a spec's free-form prose
elsewhere agrees with `SPEC-INDEX.md`'s free-form prose about it. `STATE-OF-SPINE.md` §8
("CI gate on spec-status drift") tried exactly that, generalised across all ~80 specs, and
measured 33% precision — two of three flagged mismatches were unrelated status words
appearing in ordinary prose, and it was withdrawn as "not gate-worthy... the class stays
human." Checks 1-2 above sidestep that failure mode because they read **structured table
cells**, not prose; check 3 sidesteps it because it compares a document **against itself**
(one header line, one boolean fact about its own table) rather than classifying language in
one free-form document against another's.

Only scans `docs/specs/*.md` directly, non-recursively — deliberately, so
`docs/specs/templates/language-track.md` (§8.4's skeleton, all-placeholder phase rows) is
never treated as a real roadmap needing a `SPEC-INDEX.md` entry.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "docs" / "specs"


def set_root(path: Path | str) -> None:
    """Point every check at ``path`` instead of the checkout this file lives in."""
    global ROOT, SPECS
    ROOT = Path(path).resolve()
    SPECS = ROOT / "docs" / "specs"


PHASE_HEADER = "| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |"
STATUS_DONE = "✅"

#: The leading cell of a data row is bold phase code + a label, e.g. "**P1 Comprehension**"
#: or "**C-1 Machinery**". Captures just the code.
_PHASE_ID = re.compile(r"^\*\*([A-Za-z]+-?\d+)\b")

_DEPENDS_ON = re.compile(r"\*\*Depends on:\*\*\s*\[[^\]]+\]\(([^)]+)\)\s*merged\s*\(([^)]*)\)")
_PHASE_CODE_IN_PROSE = re.compile(r"\b([A-Za-z]-?\d+)\b")
_TOP_STATUS = re.compile(r"^\*\*Status:\*\*\s*(.+?)\.", re.M)
_STALE_HEADER_PHRASES = ("no code written", "plan for review")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


@dataclass(frozen=True)
class PhaseRow:
    doc: Path
    phase_id: str
    status: str
    started: str
    finished: str
    evidence: str


def _split_row(line: str) -> list[str] | None:
    """A table row's cells, or ``None`` if ``line`` isn't a data row (the separator, or a
    row with a different column count than the header it followed)."""
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) != 8:
        return None
    if all(re.fullmatch(r":?-+:?", c) for c in cells):
        return None  # the `|---|---|` separator
    return cells


def phase_tables() -> dict[Path, list[PhaseRow]]:
    """Every phase table under ``docs/specs/``, keyed by the document holding it.

    Deliberately locates the table by its exact header line and then reads only the
    *contiguous* run of table rows right after it — not every line that merely starts
    with ``| **P1`` anywhere in the document, which would also catch an unrelated
    decisions table (`perl-codegen-roadmap.md` has one, `| # | Decision | Options |
    Recommendation |`, whose rows also start `| **C1** |`).
    """
    tables: dict[Path, list[PhaseRow]] = {}
    if not SPECS.is_dir():
        return tables
    for doc in sorted(SPECS.glob("*.md")):
        lines = doc.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() != PHASE_HEADER:
                continue
            rows: list[PhaseRow] = []
            for row_line in lines[i + 2 :]:
                cells = _split_row(row_line)
                if cells is None:
                    break
                m = _PHASE_ID.match(cells[0])
                if not m:
                    continue
                rows.append(
                    PhaseRow(
                        doc=doc,
                        phase_id=m.group(1),
                        status=cells[4],
                        started=cells[5],
                        finished=cells[6],
                        evidence=cells[7],
                    )
                )
            if rows:
                tables[doc] = rows
            break  # one phase table per document, by convention
    return tables


def check_evidence_completeness(tables: dict[Path, list[PhaseRow]]) -> list[str]:
    problems = []
    for doc, rows in tables.items():
        for row in rows:
            if row.status == STATUS_DONE and (not row.finished or not row.evidence):
                problems.append(
                    f"{doc.name}: {row.phase_id} is marked DONE but its Finished/Evidence cell is empty"
                )
    return problems


def check_cross_spec_dependency(tables: dict[Path, list[PhaseRow]]) -> list[str]:
    problems = []
    for doc, rows in tables.items():
        text = doc.read_text(encoding="utf-8")
        m = _DEPENDS_ON.search(text)
        if not m:
            continue
        dep_target, paren = m.group(1), m.group(2)
        required = _PHASE_CODE_IN_PROSE.findall(paren)
        if not required:
            continue
        if not any(row.started for row in rows):
            continue  # nothing in this roadmap has started; the dependency isn't live yet
        dep_doc = (doc.parent / dep_target).resolve()
        dep_rows = {r.phase_id: r for r in tables.get(dep_doc, [])}
        for req in required:
            dep_row = dep_rows.get(req)
            if dep_row is None:
                problems.append(
                    f"{doc.name}: a phase has Started, but its stated dependency {dep_target}'s "
                    f"{req} isn't a phase in that document's own table"
                )
            elif dep_row.status != STATUS_DONE:
                problems.append(
                    f"{doc.name}: a phase has Started, but its stated dependency {dep_target}'s "
                    f"{req} is not DONE (status {dep_row.status!r})"
                )
    return problems


def check_top_status_freshness(tables: dict[Path, list[PhaseRow]]) -> list[str]:
    problems = []
    for doc, rows in tables.items():
        text = doc.read_text(encoding="utf-8")
        m = _TOP_STATUS.search(text)
        if not m:
            continue
        top_line = m.group(1)
        if not any(row.evidence for row in rows):
            continue
        if any(phrase in top_line.lower() for phrase in _STALE_HEADER_PHRASES):
            problems.append(
                f"{doc.name}: top **Status:** line says {top_line!r}, but the phase table "
                "already has Evidence in it — the header is stale"
            )
    return problems


def check_indexed(tables: dict[Path, list[PhaseRow]]) -> list[str]:
    spec_index = SPECS / "SPEC-INDEX.md"
    if not spec_index.is_file():
        return []
    index_text = spec_index.read_text(encoding="utf-8")
    problems = []
    for doc in tables:
        if doc == spec_index:
            continue
        if f"]({doc.name})" not in index_text:
            problems.append(f"{doc.name}: has a phase table but SPEC-INDEX.md does not link to it")
    return problems


def check_relative_links(tables: dict[Path, list[PhaseRow]]) -> list[str]:
    problems = []
    for doc in tables:
        text = doc.read_text(encoding="utf-8")
        for label, target in _LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue  # a same-document anchor
            resolved = (doc.parent / path_part).resolve()
            if not resolved.is_file():
                problems.append(f"{doc.name}: link '[{label}]({target})' does not resolve")
    return problems


CHECKS = (
    check_evidence_completeness,
    check_cross_spec_dependency,
    check_top_status_freshness,
    check_indexed,
    check_relative_links,
)


def check() -> list[str]:
    tables = phase_tables()
    problems: list[str] = []
    for one_check in CHECKS:
        problems.extend(one_check(tables))
    return problems


def main() -> int:
    tables = phase_tables()
    if "--check" not in sys.argv:
        for doc, rows in tables.items():
            print(f"\n{doc.relative_to(ROOT)}")
            for row in rows:
                started = f"started={row.started or '—':12s}"
                print(f"  {row.phase_id:10s} {row.status}  {started} finished={row.finished or '—'}")
        return 0
    problems = check()
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print(f"\nroadmap-status --check: FAILED — {len(problems)} problem(s).")
        return 1
    print(f"roadmap-status --check: OK — {len(tables)} phase table(s) checked, {len(CHECKS)} checks each.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
