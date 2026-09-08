#!/usr/bin/env python3
"""The mechanical half of the user-facing documentation audit (`/review-pr` §5).

Cross-checks what the code *registers* against what the documents *say*, so a reviewer does
not have to remember which of nine documents carries a language list. Stdlib only, regex over
source — no imports of the package, so it runs on any ref, with or without extras installed.

    python .claude/skills/review-pr/scripts/docs_audit.py [--base REF --head REF] [--strict]

Checks (each prints `[STALE]`, `[MISSING]`, or `[INFO]` lines; `--strict` exits non-zero on
STALE/MISSING):

1. front-end count — every "N front-ends" / "N language front-ends" claim (digit or word) in
   the user documents vs the length of `FRONT_ENDS` in `pkg/capabilities.py`. A line that
   carries a date or a `**x.y.z**` release stamp is history and reported as INFO; the rest as
   STALE.
2. language enumerations — a line naming four or more registered languages and omitting one.
   INFO only: codegen lists legitimately exclude comprehension-only languages; a reviewer
   decides.
3. optional extras — every language extra in `pyproject.toml` (a `tree-sitter-<grammar>` or
   `sqlglot` extra) must appear at all of its registration sites: `USER_GUIDE.md`, the
   `languages` meta-extra, `ci.yml`'s sync line (or the `dev` extra CI installs),
   `doctor.EXTRA_PROBES`, `persistence._GRAMMAR_MODULES` (grammar extras), and the mypy
   `ignore_missing_imports` override.
4. CLI commands — every `.command("name")` under `cli/` is mentioned in `CLI_REFERENCE.md`.
5. MCP tools — every key of `plugin/outputs.py:OUTPUTS` is mentioned in `CLAUDE_GUIDE.md`,
   `CODEX_GUIDE.md`, and at least one `plugins/spine/skills/*/SKILL.md`.
6. with `--base/--head`: which user documents the diff touched, for the report's table.

A finding here is a pointer for the reviewer, not a verdict: a stale count in a paragraph
describing a dated measurement may be correct history. The reviewer opens the line.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src" / "orchestrator"

USER_DOCS = [
    "README.md",
    "FEATURES.md",
    "USER_GUIDE.md",
    "KNOWLEDGE_GRAPH.md",
    "CLAUDE_GUIDE.md",
    "CODEX_GUIDE.md",
    "CLI_REFERENCE.md",
    "SETUP.md",
    "EXAMPLE.md",
    "BENCHMARK.md",
    "OPERATIONS.md",
    "CONTRIBUTING.md",
    "docs/specs/STATE-OF-SPINE.md",
    "docs/specs/SPEC-INDEX.md",
    "corpus/README.md",
]
USER_DOC_GLOBS = ["plugins/spine/**/*.md"]

DISPLAY = {
    "python": "Python",
    "java": "Java",
    "typescript": "TypeScript",
    "csharp": "C#",
    "c": "C",
    "cpp": "C++",
    "go": "Go",
    "php": "PHP",
    "sql": "SQL",
    "perl": "Perl",
    "ruby": "Ruby",
    "rust": "Rust",
    "kotlin": "Kotlin",
}
WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
# Lines that describe a moment rather than the present: a date, or a release stamp.
_HISTORY = re.compile(r"20\d\d-\d\d-\d\d|\*\*\d+\.\d+\.\d+\*\*")
_COUNT = re.compile(r"\b(?:all |the other |other )?(\d+|[a-z]+) (?:language )?front-ends\b", re.I)
# Extras that bundle others or are tooling, never a language.
_NOT_LANGUAGE_EXTRAS = frozenset({"dev", "languages", "all"})


def _read(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def user_docs() -> list[str]:
    out = [d for d in USER_DOCS if (ROOT / d).is_file()]
    for pattern in USER_DOC_GLOBS:
        out.extend(sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern)))
    return out


def front_ends() -> list[str]:
    text = _read("src/orchestrator/pkg/capabilities.py")
    if "FRONT_ENDS" not in text:
        return []
    block = text.split("FRONT_ENDS", 1)[1].split(")\n\n", 1)[0]
    return re.findall(r'FrontEnd\("([a-z]+)"', block)


def check_counts(n: int) -> list[str]:
    out: list[str] = []
    for doc in user_docs():
        for i, line in enumerate(_read(doc).splitlines(), 1):
            for m in _COUNT.finditer(line):
                tok = m.group(1).lower()
                val = int(tok) if tok.isdigit() else WORDS.get(tok)
                if val is None:
                    continue
                other = "other" in m.group(0).lower()
                expected = n - 1 if other else n
                if val == expected:
                    continue
                tag = "INFO" if _HISTORY.search(line) else "STALE"
                note = f" (other → {expected})" if other else ""
                out.append(
                    f"[{tag}] front-end count: {doc}:{i} says {tok!r}, registry has {n}{note}: "
                    f"{line.strip()[:100]}"
                )
    return out


def check_enumerations(langs: list[str]) -> list[str]:
    out: list[str] = []
    names = {lang: DISPLAY.get(lang, lang) for lang in langs}
    for doc in user_docs():
        for i, line in enumerate(_read(doc).splitlines(), 1):
            present = {
                lang
                for lang, name in names.items()
                if re.search(rf"(?<![\w#+]){re.escape(name)}(?![\w#+])", line)
            }
            if len(present) >= 4:
                missing = [names[lang] for lang in langs if lang not in present]
                if missing:
                    out.append(
                        f"[INFO] enumeration omits {', '.join(missing)}: {doc}:{i}: {line.strip()[:100]}"
                    )
    return out


def _optional_extras() -> dict[str, str]:
    py = _read("pyproject.toml")
    if "[project.optional-dependencies]" not in py:
        return {}
    opt = py.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    return {m.group(1): m.group(2) for m in re.finditer(r"^([a-z-]+) = \[(.*?)\]", opt, re.S | re.M)}


def check_extras() -> list[str]:
    out: list[str] = []
    extras = _optional_extras()
    lang_extras = {
        k: v
        for k, v in extras.items()
        if k not in _NOT_LANGUAGE_EXTRAS and (re.search(r"tree-sitter-[a-z]", v) or "sqlglot" in v)
    }
    languages_meta = extras.get("languages", "")
    dev_body = extras.get("dev", "")
    sync = " ".join(re.findall(r"--extra [a-z-]+", _read(".github/workflows/ci.yml")))
    doctor = _read("src/orchestrator/doctor.py")
    persist = _read("src/orchestrator/pkg/persistence.py")
    grammar_block = ""
    if "_GRAMMAR_MODULES = (" in persist:
        grammar_block = persist.split("_GRAMMAR_MODULES = (", 1)[1].split(")", 1)[0]
    py = _read("pyproject.toml")
    mypy = re.search(r"\[\[tool\.mypy\.overrides\]\]\nmodule = \[(.*?)\]", py, re.S)
    mypy_mods = mypy.group(1) if mypy else ""
    guide = _read("USER_GUIDE.md")
    meta_names = re.findall(r"[a-z-]+", languages_meta.split("[", 1)[-1])
    for extra, body in sorted(lang_extras.items()):
        grammar = re.search(r'"(tree-sitter-[a-z-]+)', body)
        module = grammar.group(1).replace("-", "_") if grammar else None
        packages = re.findall(r'"([a-z-]+)', body)
        if f"[{extra}]" not in guide:
            out.append(f"[MISSING] extra {extra!r}: not mentioned as `[{extra}]` in USER_GUIDE.md")
        if extra not in meta_names:
            out.append(f"[MISSING] extra {extra!r}: absent from the `languages` meta-extra in pyproject.toml")
        in_dev = all(f'"{p}' in dev_body for p in packages)
        if f"--extra {extra}" not in sync and not in_dev:
            out.append(
                f"[MISSING] extra {extra!r}: absent from ci.yml's `uv sync` line (its tests skip in CI)"
            )
        if f'"{extra}":' not in doctor:
            out.append(f"[MISSING] extra {extra!r}: absent from doctor.EXTRA_PROBES")
        if module and module not in grammar_block:
            out.append(
                f"[MISSING] extra {extra!r}: {module} absent from persistence._GRAMMAR_MODULES — "
                "a warm cache ignores whether it is installed"
            )
        if module and module not in mypy_mods:
            out.append(
                f"[MISSING] extra {extra!r}: {module} absent from the mypy ignore_missing_imports override"
            )
    return out


def check_cli() -> list[str]:
    out: list[str] = []
    ref = _read("CLI_REFERENCE.md")
    for path in sorted((SRC / "cli").glob("*.py")):
        for m in re.finditer(r'@(\w+)\.command\("([a-z-]+)"', path.read_text(encoding="utf-8")):
            app, cmd = m.group(1), m.group(2)
            group = app.removesuffix("_app").replace("_", " ")
            if not re.search(rf"\b{re.escape(cmd)}\b", ref):
                out.append(f"[MISSING] CLI command `{group} {cmd}` ({path.name}) not in CLI_REFERENCE.md")
    return out


def check_mcp() -> list[str]:
    out: list[str] = []
    outputs = _read("src/orchestrator/plugin/outputs.py")
    marker = "OUTPUTS: dict[str, type] = {"
    block = outputs.split(marker, 1)[1].split("}", 1)[0] if marker in outputs else ""
    tools = re.findall(r'^\s+"([a-z_]+)":', block, re.M)
    guides = {g: _read(g) for g in ("CLAUDE_GUIDE.md", "CODEX_GUIDE.md")}
    skills = "\n".join(p.read_text(encoding="utf-8") for p in ROOT.glob("plugins/spine/skills/*/SKILL.md"))
    for tool in tools:
        for g, text in guides.items():
            if tool not in text:
                out.append(f"[MISSING] MCP tool `{tool}` not mentioned in {g}")
        if tool not in skills:
            out.append(f"[INFO] MCP tool `{tool}` not named in any plugins/spine/skills/*/SKILL.md")
    return out


def touched_docs(base: str, head: str) -> list[str]:
    try:
        names = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--name-only", f"{base}..{head}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (subprocess.CalledProcessError, OSError) as exc:
        return [f"[INFO] could not diff {base}..{head}: {exc}"]
    docs = [n for n in names if n.endswith((".md", ".svg"))]
    return [f"[INFO] docs touched by the diff ({len(docs)}): " + ", ".join(docs)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    langs = front_ends()
    lines: list[str] = [f"[INFO] registered front-ends ({len(langs)}): {', '.join(langs)}"]
    lines += check_counts(len(langs))
    lines += check_enumerations(langs)
    lines += check_extras()
    lines += check_cli()
    lines += check_mcp()
    if args.base and args.head:
        lines += touched_docs(args.base, args.head)
    for line in lines:
        print(line)
    bad = sum(1 for line in lines if line.startswith(("[STALE]", "[MISSING]")))
    info = sum(1 for line in lines if line.startswith("[INFO]"))
    print(f"\ndocs_audit: {bad} STALE/MISSING, {info} INFO")
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
