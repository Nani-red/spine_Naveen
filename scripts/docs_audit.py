#!/usr/bin/env python3
"""The mechanical half of the user-facing documentation audit (`docs/reviewing/docs-matrix.md`).

Cross-checks what the code *registers* against what the documents *say*, so a reviewer does
not have to remember which of nine documents carries a language list. Stdlib only, regex over
source — no imports of the package, so it runs on any ref, with or without extras installed.

    python scripts/docs_audit.py [--base REF --head REF] [--strict] [--removed "name,synonym"]

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
7. links — every relative link in the user documents resolves: the file exists and, when
   there is an anchor, it matches a heading under GitHub's slug rules (an em dash in a
   heading yields a double hyphen: "Step 1 — Install" → `step-1--install`). MISSING.
8. removed surfaces — with `--base/--head`, whatever the diff removed from the registries
   (a CLI command, an MCP tool, an optional extra), plus anything passed as
   `--removed "terminal UI,TUI,Textual"`, is grepped for in the user documents at head.
   A mention is STALE; inside a version-stamped or dated paragraph it is INFO (history).
   The manual list exists because a removed feature often has no registry entry — the
   terminal UI had none, and two README sentences outlived it by two releases.

`--root DIR` points every check at another tree (the tests use a fixture tree).

A finding here is a pointer for the reviewer, not a verdict: a stale count in a paragraph
describing a dated measurement may be correct history. The reviewer opens the line.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "orchestrator"


def set_root(path: Path | str) -> None:
    """Point every check at ``path`` instead of the checkout this file lives in."""
    global ROOT, SRC
    ROOT = Path(path).resolve()
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
# A date, a bold release stamp (`**3.32.0**`, `**3.32.0 (current)**`), or prose that pins a
# version ("removed in 3.31.0", "shipped 1.18.0", "since 3.29"). Present-tense prose has none.
_HISTORY = re.compile(
    r"20\d\d-\d\d-\d\d"
    r"|\*\*\d+\.\d+\.\d+[^*\n]*\*\*"
    r"|\b(?:removed|shipped|added|landed|since|until|before|after|in|at)(?: in)? v?\d+\.\d+(?:\.\d+)?\b",
    re.I,
)


def history_lines(text: str) -> set[int]:
    """1-based line numbers that are history: the line carries a date or release stamp, or
    the paragraph it sits in opens with one ("What's new" stamps the paragraph, not every
    line)."""
    out: set[int] = set()
    lines = text.splitlines()
    para_start = 0
    para_history = False
    for i, line in enumerate(lines, 1):
        if not line.strip():
            para_start, para_history = 0, False
            continue
        if para_start == 0:
            para_start, para_history = i, bool(_HISTORY.search(line))
        if para_history or _HISTORY.search(line):
            out.add(i)
    return out


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
        text = _read(doc)
        history = history_lines(text)
        for i, line in enumerate(text.splitlines(), 1):
            for m in _COUNT.finditer(line):
                tok = m.group(1).lower()
                val = int(tok) if tok.isdigit() else WORDS.get(tok)
                if val is None:
                    continue
                other = "other" in m.group(0).lower()
                expected = n - 1 if other else n
                if val == expected:
                    continue
                tag = "INFO" if i in history else "STALE"
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


# --------------------------------------------------------------------------- #
# 7. links
# --------------------------------------------------------------------------- #

_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def github_slug(heading: str) -> str:
    """GitHub's anchor for a heading: markdown stripped, lowercased, everything but letters,
    digits, spaces, hyphens and underscores dropped, spaces to hyphens. Consecutive hyphens
    are kept — that is where the double hyphen for an em dash comes from."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](link) → text
    text = text.replace("**", "").replace("*", "").replace("_", "_")
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = text.replace(" ", "-")
    return text


def heading_anchors(text: str) -> set[str]:
    """Every anchor a document offers, duplicates suffixed the way GitHub does (-1, -2, …).
    Headings inside code fences are not headings."""
    seen: dict[str, int] = {}
    out: set[str] = set()
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _HEADING.match(line)
        if not m:
            continue
        slug = github_slug(m.group(2))
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


def _links_in(text: str) -> list[tuple[int, str]]:
    """``(line, target)`` for every link outside a code fence; inline code is skipped too."""
    out: list[tuple[int, str]] = []
    fenced = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        bare = re.sub(r"`[^`]*`", "", line)
        for m in _LINK.finditer(bare):
            out.append((i, m.group(1)))
    return out


def check_links() -> list[str]:
    out: list[str] = []
    for doc in user_docs():
        text = _read(doc)
        for lineno, target in _links_in(text):
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("//"):
                continue  # http(s), mailto:, and friends
            path, _, anchor = target.partition("#")
            if not path and not anchor:
                continue
            base = (ROOT / doc).parent
            file = (base / path).resolve() if path else (ROOT / doc)
            if not file.exists():
                out.append(f"[MISSING] link: {doc}:{lineno} → {target} — file not found")
                continue
            if anchor and file.suffix == ".md":
                anchors = heading_anchors(file.read_text(encoding="utf-8", errors="replace"))
                if anchor not in anchors:
                    stem = anchor.split("-")[0]
                    near = sorted(a for a in anchors if a.startswith(stem))[:3]
                    hint = f" (nearest: {', '.join(near)})" if near else ""
                    out.append(f"[MISSING] link: {doc}:{lineno} → {target} — no such anchor{hint}")
    return out


# --------------------------------------------------------------------------- #
# 8. removed surfaces
# --------------------------------------------------------------------------- #


def cli_commands_in(text: str) -> set[str]:
    return set(re.findall(r'@\w+\.command\("([a-z-]+)"', text))


def mcp_tools_in(outputs_text: str) -> set[str]:
    marker = "OUTPUTS: dict[str, type] = {"
    block = outputs_text.split(marker, 1)[1].split("}", 1)[0] if marker in outputs_text else ""
    return set(re.findall(r'^\s+"([a-z_]+)":', block, re.M))


def extras_in(pyproject_text: str) -> set[str]:
    if "[project.optional-dependencies]" not in pyproject_text:
        return set()
    opt = pyproject_text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    return set(re.findall(r"^([a-z-]+) = \[", opt, re.M)) - _NOT_LANGUAGE_EXTRAS


def _at_ref(ref: str, rel: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{ref}:{rel}"], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return ""


def _cli_files_at(ref: str) -> list[str]:
    try:
        names = subprocess.run(
            ["git", "-C", str(ROOT), "ls-tree", "--name-only", ref, "src/orchestrator/cli/"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (subprocess.CalledProcessError, OSError):
        return []
    return [n for n in names if n.endswith(".py")]


def removed_surfaces(base: str, head: str) -> dict[str, set[str]]:
    """What ``head`` no longer registers that ``base`` did: CLI commands, MCP tools, extras."""

    def registries(ref: str) -> dict[str, set[str]]:
        cli = set()
        for f in _cli_files_at(ref):
            cli |= cli_commands_in(_at_ref(ref, f))
        return {
            "CLI command": cli,
            "MCP tool": mcp_tools_in(_at_ref(ref, "src/orchestrator/plugin/outputs.py")),
            "extra": extras_in(_at_ref(ref, "pyproject.toml")),
        }

    before, after = registries(base), registries(head)
    return {kind: before[kind] - after[kind] for kind in before if before[kind] - after[kind]}


def check_removed(terms: dict[str, str]) -> list[str]:
    """``terms`` maps a phrase to what it is (``"tui": "CLI command"``, ``"terminal ui":
    "removed surface"``). Every mention in the user documents is STALE, or INFO inside a
    history paragraph."""
    out: list[str] = []
    for doc in user_docs():
        text = _read(doc)
        history = history_lines(text)
        for i, line in enumerate(text.splitlines(), 1):
            for term, kind in terms.items():
                if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", line, re.I):
                    tag = "INFO" if i in history else "STALE"
                    out.append(
                        f"[{tag}] removed {kind} {term!r} still mentioned: {doc}:{i}: {line.strip()[:100]}"
                    )
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
    ap.add_argument("--root", help="audit another tree (default: this checkout)")
    ap.add_argument(
        "--removed",
        default="",
        help='comma-separated names/synonyms of removed features to grep for, e.g. "terminal UI,TUI,Textual"',
    )
    args = ap.parse_args()
    if args.root:
        set_root(args.root)

    langs = front_ends()
    lines: list[str] = [f"[INFO] registered front-ends ({len(langs)}): {', '.join(langs)}"]
    lines += check_counts(len(langs))
    lines += check_enumerations(langs)
    lines += check_extras()
    lines += check_cli()
    lines += check_mcp()
    lines += check_links()
    terms: dict[str, str] = {t.strip(): "surface" for t in args.removed.split(",") if t.strip()}
    if args.base and args.head:
        lines += touched_docs(args.base, args.head)
        for kind, names in removed_surfaces(args.base, args.head).items():
            lines.append(
                f"[INFO] {kind}s removed between {args.base} and {args.head}: {', '.join(sorted(names))}"
            )
            terms.update({n: kind for n in names})
    if terms:
        lines += check_removed(terms)
    for line in lines:
        print(line)
    bad = sum(1 for line in lines if line.startswith(("[STALE]", "[MISSING]")))
    info = sum(1 for line in lines if line.startswith("[INFO]"))
    print(f"\ndocs_audit: {bad} STALE/MISSING, {info} INFO")
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
