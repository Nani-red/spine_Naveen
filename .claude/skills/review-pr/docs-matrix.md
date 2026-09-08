# User-facing documentation matrix — what a change obliges you to update

Read across: if the diff matches the trigger, every document in the row must change in this
PR, and say the right thing. "Says the right thing" means the reviewer opens the line, not
just sees the file in the diff. Root-level `*.md` files are the user documentation; `docs/specs/`
are design records and count separately.

| Trigger in the diff | Must update | What to check on the line |
|---|---|---|
| Any user-visible behaviour change | `CHANGELOG.md` (Unreleased / this version) | one entry, names the command or surface, links the spec |
| New language front-end (`pkg/*_extractor.py`) | `README.md` (**every** language list — there are at least three: the intro paragraph, the "Works across" line, the "Add a language" table row with its count), `FEATURES.md` (capability row **and** the "all N front-ends" accuracy row), `USER_GUIDE.md` (**both** passages: the extras list near the top and the "Multi-language" blockquote, plus the corpus-results line), `KNOWLEDGE_GRAPH.md` (node matrix, edge matrix, language table, "Parser coverage" paragraph, fact-mapping notes), `CLAUDE_GUIDE.md` and `CODEX_GUIDE.md` (language sentence, "N front-ends", toolchain table if codegen), `CLI_REFERENCE.md` (corpus results count), `EXAMPLE.md`, `BENCHMARK.md` ("other N front-ends"), `SETUP.md` if an extra is documented there, `plugins/spine/skills/*/SKILL.md` language line, `corpus/README.md` id-vocabulary row, `docs/specs/STATE-OF-SPINE.md` (front-end row **and** the precision row that says "all N front-ends"), `docs/specs/language-expansion-roadmap.md` status, `assets/spine-architecture.svg` via its script | run `scripts/docs_audit.py`; then grep the number word ("eight", "nine") as well as the digit |
| New optional extra in `pyproject.toml` | `USER_GUIDE.md` extras list, `SETUP.md`, the `languages`/`all` meta-extras, `.github/workflows/ci.yml` sync line, `doctor.EXTRA_PROBES`, `persistence._GRAMMAR_MODULES` (grammar extras), mypy `ignore_missing_imports` override | the audit script checks all six sites |
| New or changed CLI command / flag (`src/orchestrator/cli/*.py`) | `CLI_REFERENCE.md` (section + command map), `USER_GUIDE.md` if it is a user workflow, `CLAUDE_GUIDE.md`/`CODEX_GUIDE.md` if the guides walk through it, `docs/specs/STATE-OF-SPINE.md` CLI-commands count | the audit script checks presence; you check the flags are described |
| New or changed MCP tool (`src/orchestrator/plugin/server.py`) | `CLAUDE_GUIDE.md` and `CODEX_GUIDE.md` tool tables, `plugins/spine/skills/*/SKILL.md` tool lists, `docs/specs/mcp-plugin-surface.md`, `plugin/outputs.py` output type | name, one-line purpose, read-only column |
| New node or edge kind in `pkg/facts.py` | `KNOWLEDGE_GRAPH.md` matrices, `corpus/README.md` decided rules, `FEATURES.md`, `docs/specs/PRODUCT-KNOWLEDGE-GRAPH.md`, `assets/spine-architecture.svg` (it states "N node kinds · M edge kinds") | the SVG check script fails if not re-rendered |
| New `docs/specs/*.md` | `docs/specs/SPEC-INDEX.md` (row **and** the count in prose **and** the `ls … wc -l` line), `docs/specs/README.md`, `docs/specs/STATE-OF-SPINE.md` spec-file count | `state-numbers.py --check` gates all three counts |
| Any spec whose status changed | that spec's status line, `SPEC-INDEX.md` row, `STATE-OF-SPINE.md` progress table, any sibling spec that restates it (e.g. the expansion roadmap restating a language roadmap) | statuses must agree with each other and with the code |
| Registry / web UI change (`registry/`) | `OPERATIONS.md`, `USER_GUIDE.md` operator section, `docs/specs/unified-ui.md` | no build step introduced |
| Deploy / env / config change | `OPERATIONS.md`, `SETUP.md`, `.env.example` if present | variable name and default |
| Contribution process change | `CONTRIBUTING.md`, `.github/pull_request_template.md` | |
| Release cut | `pyproject.toml` version, `CHANGELOG.md` header, `README.md` "What's new", both SVGs, `STATE-OF-SPINE.md` version row, `grep` for the previous version string across `*.md` | see SKILL.md §7 |
| New maintainer tooling (`.claude/skills`, `.claude/agents`, `scripts/`) | `CONTRIBUTING.md` (how to run it) | one line is enough |

## Counts that rot

These appear as prose and rot silently. After any change to what they count, grep for the
digit **and** the word:

- number of language front-ends (`FRONT_ENDS` in `pkg/capabilities.py`)
- number of corpus fixture cases (`ls corpus/*/*/expected.json | wc -l`)
- number of MCP tools; number of CLI commands; number of specs; test counts
- "N node kinds · M edge kinds" in the architecture SVG

`scripts/state-numbers.py --check` gates some of these; the audit script covers the rest.
