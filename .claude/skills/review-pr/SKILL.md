---
name: review-pr
description: >-
  Maintainer review of a pull request into this repo — the repeatable checklist a
  merge/promotion decision needs: process and governance, the local quality gate with CI's
  exact extras, deep code review fanned out to subagents (precision-first, no invented facts,
  determinism, import cycles), the accuracy corpus rule, a real-repository smoke test when a
  front-end changed, and a mechanical audit that every user-facing document a change touches
  was actually updated. Use for "review PR 334", "is this MR ready to merge / promote",
  "review this branch before release". Verdict at the end, findings ranked, docs table.
argument-hint: "<pr-number | branch> [--promote] [--comment]"
---

# Review a pull request into Spine

`$ARGUMENTS` — a PR number (preferred), or a branch name. `--promote` means the question is
"is `develop` ready to become a release on `main`", which adds the release-cut checks in §7.
`--comment` means post the finished review as one PR comment; never post without it.

**Rules that override everything below**
- Findings must carry `file:line` and a concrete failure scenario. No finding without one.
- Run commands with `uv run --frozen`. A bare `uv run` re-syncs the environment to the
  lockfile's default extras and can **uninstall a language grammar mid-review**, which
  silently changes what the extractor emits and the persistence cache key.
- Never write a validation repository's name into `src/` or `tests/`; `docs/` only.
- Never commit `episteme/`, and never edit the PR branch during a review. Findings go in the
  report; fixes go in a separate PR when asked.
- Read [docs-matrix.md](docs-matrix.md) before §5 and
  [language-frontend-checklist.md](language-frontend-checklist.md) when a front-end changed.

## 0. Identify what you are reviewing

```bash
gh pr view <N> --json number,title,author,baseRefName,headRefName,state,isDraft,mergedAt,mergedBy,reviewDecision,statusCheckRollup,commits,files,body
gh api repos/synaptixs/spine/collaborators/<login>/permission --jq '{permission,role_name}'
gh api repos/synaptixs/spine/pulls/<N>/reviews --jq '.[]|{user:.user.login,state}'
gh api graphql -f query='{repository(owner:"synaptixs",name:"spine"){pullRequest(number:<N>){reviewThreads(first:50){nodes{isResolved path comments(first:1){nodes{author{login} body}}}}}}}'
gh api "repos/synaptixs/spine/code-scanning/alerts?state=open&ref=refs/heads/<head-or-base>&per_page=100"
gh pr diff <N> > <scratchpad>/pr<N>.diff
```

Record, in the report's first lines: open or **already merged** (and by whom — an author
self-merge with no maintainer review is a finding on its own), base/head, the contributor's
permission level, whether the PR body's template placeholders (`<!-- … -->`) were left in and
the "How it was tested" section is empty, unresolved review threads, and open code-scanning
alerts on the changed files. CONTRIBUTING says an **unresolved review thread blocks the
promotion PR even at Note severity**, and code scanning re-comments on the release PR for
anything new relative to `main` — so an unresolved CodeQL note on `develop` is a promotion
blocker, not cosmetics.

Classify every changed file: `src/`, `tests/`, `corpus/`, `docs/specs/`, root user docs,
generated artifacts (`assets/*.svg`, `scoreboard.json`, `uv.lock`), CI, and **forbidden**
(`episteme/` anywhere in the diff fails the review outright).

## 1. Get the code locally without disturbing the working tree

Prefer a worktree: `git worktree add <scratchpad>/pr<N> <head-ref>` (for a merged PR review
the merge commit on `develop`). If you must use the main checkout, move any untracked files
that would collide out to the scratchpad first, and put them back at the end. Note that an
**untracked `docs/specs/*.md` of your own changes the spec-file count** and will fail
`state-numbers.py --check` for reasons unrelated to the PR — check `git status` before
blaming the PR for a count.

Sync exactly what CI syncs (read the list from `.github/workflows/ci.yml`, do not assume):

```bash
uv sync $(grep -A3 'uv sync --extra' .github/workflows/ci.yml | grep -oE -- '--extra [a-z-]+' | tr '\n' ' ')
```

## 2. The gate, as CI runs it

All of these, and report each result verbatim, green or not:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests
uv run --frozen python scripts/render_architecture_svg.py --check
uv run --frozen python scripts/render_knowledge_foundation_svg.py --check
uv run --frozen python scripts/matrix-count.py --check
uv run --frozen python scripts/state-numbers.py --check
uv run --frozen orchestrator pkg accuracy --check
uv run --frozen orchestrator pkg verify .
uv run --frozen pytest -q -p no:cacheprovider -o addopts=""
```

Run pytest **without `-x`** and read the summary line, not the exit code: a `-x` run stops at
the first error and can still exit 0. `tests/integration/` needs Postgres on `127.0.0.1:5433`;
a `psycopg.OperationalError` there is environmental — say so and cite the CI check instead,
do not report the suite as passing. Language-extra tests `importorskip`, so a skip count that
jumped is a missing extra, not a pass.

When `scoreboard.json` changed: the per-language corpus blocks of **other** languages must be
byte-identical; the repo-self numbers (provenance, drift, parity, invention totals) move with
ordinary code and are not findings, but a PR claiming "the repo's own graph is unchanged"
while they moved is misstating its evidence.

## 3. Deep review — fan out, do not read 3,000 lines serially

Launch subagents in parallel (`general-purpose`, background, READ-ONLY), one per new or
heavily changed module, and one for tests-plus-corpus. Each prompt must name: the files, the
spec in `docs/specs/` the PR claims to implement, the shipped template it should mirror, the
invariants from `CLAUDE.md`, and the exact questions below. Ask for ranked findings with
`file:line`, a "what is good" paragraph, and a one-line verdict.

Questions every code-review agent answers:
1. **Fabrication.** Any path that emits a node or edge the source does not prove: guessed
   namespaces, dynamic names (`$obj->$m()`, reflection, string callables), `self`/`static`/
   `parent` treated as class names, computed paths or prefixes emitted as literal, group
   prefixes dropped. The `invention` gate is strict at zero per language; a single invented
   edge is a high finding.
2. **Spec conformance.** Each decision (D-rows) as built vs as written; any deviation the
   spec's "as built" notes do not record.
3. **Determinism.** Randomness, timestamps, set/dict iteration order, filesystem order.
4. **Import cycles** (CodeQL `py/cyclic-import`): module-level vs function-local imports;
   whether the precedent (`go_routes.py` imports nothing from its extractor) was followed.
5. **Error tolerance**: an ERROR-laden parse still yields what parsed and never raises; no bare
   `except`.
6. **Provenance** on every grounded node; `end_line` where the template gives one.
7. **Tests**: what each stated rule lacks a test for; magic counts; tests that pass by accident.
8. **mypy --strict** hygiene and `# type: ignore` counts; repo style (why-docstrings, no
   TODO/print/dead code).

Questions the tests-and-corpus agent answers (read `corpus/README.md` first):
- Labels written **from source**, not from extractor output: derive the true fact set from the
  fixture by hand and diff it against `expected.json`; a case whose omissions coincide exactly
  with what the extractor skips is the self-agreeing corpus the README forbids.
- `known_gaps` predicted, with reasons that match the spec; `open_questions` empty;
  `excluded` records builtins and other deliberate non-labels the way sibling cases do.
- Each case exercises the finding the spec says it exists to catch (a "false positive" case
  must contain the shape whose wrong answer would score).
- Id vocabulary per the README table.

You may also run `/code-review <N> high` for an independent correctness pass; it does not
replace the questions above.

## 4. Real-repository smoke test (any change to a front-end, a routes/ORM pass, a post-pass)

Shallow-clone one public repository the relevant spec names into the scratchpad. Run against
a **copy with `.git` removed** so no commit-keyed cache can be trusted, then delete both:

```bash
uv run --frozen orchestrator pkg extract <copy> --json   # node kinds by language; Endpoint/Entity present?
uv run --frozen orchestrator pkg verify <copy>            # errors are findings; say which are pre-existing
uv run --frozen orchestrator state <copy> --lens developer | grep -E "^- Stack|^- Size|Call graph"
```

Compare against a targeted extract of one or two files. A whole-repo result that contradicts
a single-file result is a cache or ordering problem — locate it, do not average it away.
Check `~/.cache/orchestrator/pkg` entries for the clone (repo-key = sha256 of the resolved
path) and whether `persistence._GRAMMAR_MODULES` lists the new grammar; if it does not, a
warm cache serves a graph without that language forever, silently.

## 5. User-facing documentation audit — mandatory, every PR

Run the mechanical half first, then the judgement half.

```bash
uv run --frozen python .claude/skills/review-pr/scripts/docs_audit.py --base <base-ref> --head <head-ref>
```

It reports: stale "N front-ends" counts against the registry, language enumerations that omit
a registered language (informational — codegen lists legitimately exclude comprehension-only
languages), CLI commands missing from `CLI_REFERENCE.md`, MCP tools missing from the two guides
and the plugin skill, every optional extra missing from any of its registration sites
(`USER_GUIDE.md`, the `languages` meta-extra, `ci.yml`, `doctor.EXTRA_PROBES`,
`persistence._GRAMMAR_MODULES`, the mypy override), **every relative link that does not
resolve** (file or anchor, under GitHub's slug rules), and — with `--base/--head` — **every
mention of a surface the diff removed** (a CLI command, an MCP tool, an extra). A removed
feature often has no registry entry, so name it and its synonyms yourself:

```bash
uv run --frozen python .claude/skills/review-pr/scripts/docs_audit.py --base <base> --head <head> --removed "terminal UI,TUI,Textual"
```

A mention inside a version-stamped or dated paragraph is reported as INFO — it is history and
may stay; a mention in present-tense prose is STALE.

Then walk [docs-matrix.md](docs-matrix.md): for each trigger the diff matches, confirm the
named document changed **and says the right thing** — a language added to one list and
missing from the same document's second list is a finding with both line numbers. Include a
table in the report: trigger · document · updated? · line.

Also check internal consistency across specs: a status line in one spec ("all four phases
done") that another spec or `SPEC-INDEX.md` contradicts ("P1+P2") is a finding.

## 6. Repo invariants to check by hand (from `CLAUDE.md`)

PKG is the only source of truth (renderers never re-derive facts from paths); `understand` /
`state` have no LLM call, randomness, or timestamp; any layout is seeded and computed;
the web UI gained no build step or graph library; shared artifacts inline their CSS;
grouping is by owning module, never by symbol id; aggregations record what they elided;
caches are commit-keyed **and keyed on the extractor fingerprint**; a changed Protocol updated
its test fakes; fixture source lives under a dot-prefixed root; `--language` stays validated
(a comprehension-only language must **not** be added to `SUPPORTED_LANGUAGES`).

## 7. Promotion (`--promote`) — the release cut

- Version bumped in `pyproject.toml` **and the lockfile's own package entry only** (a full
  `uv lock` on a developer machine rewrites unrelated markers and can downgrade the lock
  revision — revert that, change the one line, as every prior cut did); `CHANGELOG.md` has a
  dated header, not just "Unreleased"; `README.md` "What's new" names the release; both SVGs
  re-rendered (they stamp the version); `STATE-OF-SPINE.md` version row and the numbers
  `state-numbers.py` derives.
- **Every plugin manifest** — the list is `_MANIFESTS` in `tests/plugin/test_manifests.py`
  (three today, including the root `.claude-plugin/marketplace.json`, which a `find` for
  `plugin.json` does not return). Run that test file; the 3.33.0 cut failed CI on the one
  the grep below cannot see.
- `grep -rn "<previous version>" *.md docs/specs/*.md` for stale version strings in prose,
  and `grep -rn '"version": "<previous version>"' .claude-plugin plugins codex-marketplace`
  for the JSON the Markdown grep misses.
- Every open code-scanning alert on the changed files resolved or fixed (they re-comment on
  the release PR and block it).
- `develop` is fast-forwardable from the reviewed merge commit; nothing landed since.

## 8. The report

Lead with the verdict on the first line — one of: **Ready to merge** / **Mergeable with
fixes (listed)** / **Not mergeable**, and for `--promote`: **Ready to promote** / **Not ready
to promote: fix-forward PR required**. Then:

1. Process facts (§0) in three or four lines.
2. Findings ranked most severe first, each `file:line` + scenario; group as
   blocking / should-fix / nits. Attribute which agent or command produced each.
3. Gate table: command → result (verbatim summary line).
4. Docs audit table (§5).
5. What is good — one paragraph, specific.
6. Ordered next steps, ending with a single recommendation. No menus.

Say what you could not verify and why. Say what was environmental. Never report a suite as
passing from an exit code.
