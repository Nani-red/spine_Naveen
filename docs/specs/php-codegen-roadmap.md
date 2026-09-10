# Design + Plan: PHP codegen — the profile the PHP track deferred

**Status:** P0–P5 exit criteria completed on `codex/php-codegen` (2026-09-10).
Recommendations D1–D7 accepted by the maintainer. [Merge PR #350](https://github.com/synaptixs/spine/pull/350)
contains the implementation, tests and documentation; its checks track final merge readiness. Follow-on to
[`php-support-roadmap.md`](php-support-roadmap.md) decision **D6**, which shipped PHP as the
9th graph language and deliberately left `"php"` out of `SUPPORTED_LANGUAGES` so that
`sdlc feature --language php` exits 2 instead of scaffolding Python. This record reverses
D6 with the full runner set the Go track proved is the minimum
([`go-support-roadmap.md`](go-support-roadmap.md), phases 4.2–4.5).

**Validation target:** [`synaptixs/aiemr`](https://github.com/synaptixs/aiemr), public. Chosen
by the maintainer; measured below, and it is not the repository this plan would have picked,
which is exactly why it is the right one.

## 0. Decisions surfaced up front

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | Test framework | (a) PHPUnit only, (b) PHPUnit + Pest | **(a).** `catalog/profile.py` already detects both; Pest is a PHPUnit wrapper and the runner can grow a `pest` branch later without a second environment. |
| **D2** | Dependency manager | (a) Composer required, (b) Composer when present, PHPUnit **phar** when not | **(b).** The validation target has no root `composer.json` (§1). A profile that only works with Composer would not run on the repository it is being built for. |
| **D3** | Greenfield scaffold | (a) plain Composer package, (b) Laravel-aware | **(a).** PSR-4 `src/` + `tests/`, `composer.json`, `phpunit.xml`. Laravel projects are brownfield and carry their own layout; a framework scaffold is a second product. |
| **D4** | Brownfield test placement | (a) mirror the repo's `phpunit.xml` suite directory, (b) always `tests/` | **(a).** The `<directory suffix="Test.php">` element *is* the layout contract; aiemr's says `Tests`, capital T. Inventing `tests/` beside it produces a test the suite never runs — a false green. |
| **D5** | PHP version on the runner | (a) runner default (8.3 on `ubuntu-latest`), (b) pin per repository via `.php-version` / `composer.json` `require.php` / a workflow input | **(b), default (a).** OpenEMR 4.2.2 targets PHP 5.x; parts of it will not parse on 8.x. The tool environment reports the version it found, and preflight (`php -l`) tells the truth before a model runs. |
| **D6** | Preflight | (a) `php -l` on changed files only, (b) `phpstan` when the repo declares it | **(a) in P2, (b) later.** Lint is free and catches the class of error the refine loop wastes tokens on; static analysis needs a config the target does not have. |
| **D7** | What "green on aiemr" means | (a) the repository's existing suite passes, (b) the **generated** tests for the touched class pass | **(b).** The existing suite is PHPUnit 3 on PHP 5 and cannot be made green without changing the target (§1). The profile's claim is *Spine can add tested code to this repository*, and that is provable. |

## 1. Where the validation target actually is — measured 2026-09-09

`synaptixs/aiemr` is an **OpenEMR 4.2.2** fork (`version.php`: `4.2.2`, default branch
`rel-422`). GitHub reports 32.6 MB of PHP, 9 MB of JavaScript, plus Perl, Smarty, XSLT and
a little C++. Read from the API, not cloned:

| Fact | Consequence for this plan |
|---|---|
| **No root `composer.json`.** 65 hits for the filename are all vendored under `library/` and `contrib/`, which `DEFAULT_IGNORE_DIRS` does not cover (it ignores `vendor/`, not `library/`). | D2: Composer is optional. Also a comprehension note — the 65 vendored manifests and their sources will present as first-party unless `sdlc plan` is pointed below them; measure the phantom count in P0. |
| **Root `phpunit.xml`** (2010, Andrew Moore): one suite, `<directory suffix="Test.php">Tests</directory>`. | D4: the runner reads this file for the suite directory and suffix rather than assuming `tests/`. |
| **`Tests/` holds five tests + `BaseHarness.class.php`**, e.g. `NumberToTextTest.php`: `require_once 'PHPUnit/Framework.php'` and `extends PHPUnit_Framework_TestCase`. | That is **PHPUnit 3.x** style. `PHPUnit_Framework_TestCase` was removed in PHPUnit 6 (2017); `PHPUnit/Framework.php` in 3.5. Neither Composer nor a modern phar will load these tests. D7 follows. |
| **Procedural, global-function code base**, `library/*.inc` and `library/classes/*.class.php`, `mysql_*` era. | Codegen prompts must not assume namespaces or autoloading. `require_once` with a relative path is the import mechanism (the PHP graph's D7 already models it). PHP 8 removed `mysql_*`, `each()`, `create_function`: many files will not lint on 8.3, and some pure library classes will. P0 measures which. |
| `NumberToText.class.php` is a pure-PHP class with no database dependency, already under test. | The first codegen target: a change to a class that is importable, testable and side-effect free on a modern PHP. |

**What this means:** the profile is being built against a *legacy brownfield* repository,
not a Composer package. Every earlier language track validated on a modern project
(OTel-Go, a Maven app). This one validates the hardest shape first, and the greenfield
Composer path (D3) is the easy half.

## 2. Why this is cheaper than a new language

Go's delivery wiring is ~22 language-specific lines across six files plus one runner class,
one environment class, three prompt variants and a scaffold. PHP needs the same six seams
and nothing new in the graph — comprehension shipped, `pkg extract` on aiemr already
yields modules, classes, functions, `require_once` edges and calls. The two things Go did
not need:

- **a no-manifest mode** (D2): PHPUnit as a phar under the workspace when there is no
  Composer, `composer install` when there is;
- **a suite-file layout reader** (D4): `phpunit.xml` / `phpunit.xml.dist` → suite directory,
  suffix, bootstrap.

Everything else is a copy of the Go seam with the names changed.

## 3. Design

### 3.1 Layout — `layout.py`

`_resolve_php_layout(root, mode, package_name, repo)` returns a `TargetLayout` with
`language="php"`, `build_tool="composer"` or `"phar"`:

| Repo shape | Detection | `source_dir` | `tests_dir` | `mode` |
|---|---|---|---|---|
| Composer package | `composer.json` with `autoload.psr-4` | the PSR-4 directory (usually `src`) | `tests` | `existing` |
| PHPUnit config, no Composer (**aiemr**) | `phpunit.xml[.dist]` | `.` (derive per change from the graph's landing site) | the `<directory>` of the first suite (`Tests`) | `existing` |
| PHP files, no config | any `*.php` outside ignored dirs | `.` | `tests` | `existing` (no scaffold) |
| Empty | nothing | `src` | `tests` | `new` → scaffold (D3) |

`TargetLayout` gains one field, `test_suffix` (default `Test.php`), because the suite
element carries it and generated tests must match it or never run.

### 3.2 Scaffold — `scaffold.py` `_php_files`

Greenfield only (D3): `composer.json` (PSR-4 `App\\` → `src/`, `require-dev` PHPUnit
`^11`), `phpunit.xml` (one suite, `tests`, bootstrap `vendor/autoload.php`), `src/.gitkeep`,
`tests/.gitkeep`, `.gitignore` with `vendor/`. Nothing framework-shaped.

### 3.3 Tool environment — `testenv.py` `PhpToolEnvironment`

- `ensure()`: `php --version` (records major.minor, D5). With `composer.json`:
  `composer install --no-interaction --prefer-dist` (best-effort, like Go's
  `go mod download`). Without: download `phpunit.phar` for the major the repo can run
  into the **workspace root**, outside the worktree, so it is never committed and never
  walked; verify its SHA against a pinned table.
- `install()`: no-op (PHP deps are Composer's, not pip's), mirroring `GoToolEnvironment`.
- `php_toolchain_available()` → `shutil.which("php")`; `feature_runner.py` gets the same
  early refusal Go has: *"PHP codegen needs `php` on PATH"*.

### 3.4 Test runner — `testrunner.py` `PhpUnitTestRunner`

Runs **from the repo root**, `<phpunit> [--configuration <found xml>] [--filter <class>]
<test file>`, where `<phpunit>` is `vendor/bin/phpunit` when present, else the phar. Runs
only the test files this change wrote or touched (`git status --porcelain`, `*Test.php`),
the same per-change targeting Go's `_changed_modules` does — running aiemr's whole suite
would fail on files that predate PHPUnit 6 and report the target's age as the change's
fault. `passed = returncode == 0`; output clipped for the refine prompt.

### 3.5 Preflight — D6

`php -l <file>` on every changed `.php` file before tests. Free, deterministic, and on a
PHP 5-era code base it is the step that says *this file cannot run on this interpreter*
before a model is asked to fix a test that never loaded.

### 3.6 Prompts — `codegen.py`

Three variants, `_IMPLEMENT_SYSTEM_PHP`, `_TESTS_SYSTEM_PHP`, `_REFINE_SYSTEM_PHP`, and a
`layout.language == "php"` branch in the layout paragraph. What they say that Go's do not:

- brownfield: *no namespaces unless the file you are editing has one; import with
  `require_once __DIR__ . '/...'`; match the file's existing style*;
- tests: `use PHPUnit\Framework\TestCase;` (modern), placed in `tests_dir` with
  `test_suffix`, bootstrapped by `require_once` of the class under test when there is no
  autoloader;
- `declare(strict_types=1)` only in greenfield.

### 3.7 Conventions — `catalog/catalog.py`

A `php-conventions` sample the same way `go-conventions` samples: N source files and N test
files from `source_dir` / `tests_dir`, so the model sees `.class.php` naming and the
`require_once` idiom rather than being told.

## 4. Phases

| Phase | Work | Effort | Exit | Status |
|---|---|---|---|---|
| **P0 Measure** | Clone aiemr. `pkg extract` → node count, and the phantom count from `library/`/`contrib/` vendored sources (decide whether `DEFAULT_IGNORE_DIRS` grows or `--path` is scoped). `php -l` every `library/classes/*.class.php` on PHP 8.3 and on 7.4 → a table of what parses where. Run `NumberToTextTest.php` against a modern phar to record the exact failure. `sdlc plan --spec` for the P3 ticket, no model. | ~1 d | Numbers in this file's §1, and D5's default confirmed or changed | complete — measurements below; deterministic plan written |
| **P1 Runner set** | `PhpToolEnvironment`, `PhpUnitTestRunner`, `php_toolchain_available`, `make_tool_environment` / `make_test_runner` branches, the early refusal in `feature_runner.py`. Tests with a fake `php` on PATH and a real one when present (`pytest.importorskip`-style skip like the Go tests). | ~1 d | A hand-written `FooTest.php` in a temp repo with `phpunit.xml` runs green through the runner, with and without Composer | complete — fake-tool regressions and real Composer/PHAR green |
| **P2 Layout + preflight** | `_resolve_php_layout`, the `phpunit.xml` reader, `test_suffix` on `TargetLayout`, `_php_files` scaffold, `php -l` preflight. `"php"` joins `SUPPORTED_LANGUAGES` and `_resolve_language`'s chain **here**, not before — the D6 trap. | ~1 d | `sdlc feature --language php` on an empty repo scaffolds a Composer package that `composer install && vendor/bin/phpunit` accepts; on aiemr, layout reports `Tests`/`Test.php`/phar | complete — four shapes, scaffold, lint and empty-suite checks green |
| **P3 Brownfield, live** | Prompts + conventions. The ticket: *"NumberToText: support negative numbers"* (or whatever P0 shows is pure and testable) — `sdlc autorun --safe` against aiemr, then `--live` to a fork. | ~1–2 d | The generated `Tests/NumberToTextNegativeTest.php` (modern style) passes on the runner's PHP; the diff touches one class and one test; the run record shows implement → tests → refine → green | complete — model run, PHP 8.3/7.4 tests, fork PR #1 |
| **P4 Greenfield, live** | The Go 4.4 equivalent: an LLM-generated small library into an empty repo. | ~0.5 d | Composer scaffold + generated code + tests green in one run | complete — run `ffe22823b724427f`, tests/proof/review green |
| **P5 Pipeline + docs** | `sdlc-dogfood.yml` gains an aiemr row (plan-only in CI, build on dispatch — see [the reusable workflow](../../.github/workflows/spine-sdlc.yml)); `php-support-roadmap.md` §9/§11 updated; README/FEATURES/USER_GUIDE language lists say PHP builds, not only reads; `STATE-OF-SPINE` numbers. | ~0.5 d | `python scripts/sdlc_shapes.py` unchanged; the aiemr plan job green in CI | complete — docs and local gates pass; aiemr episteme/plan job green in CI |

P1 and P2 can land as one PR; P3 is the one that costs tokens and is the proof.

## 5. Files to change

**New**

| File | Phase |
|---|---|
| `src/orchestrator/sdlc/php.py` — shared config and changed-file discovery | P1–P2 |
| `.github/sdlc/php-codegen-spec.json` — pinned aiemr compatibility ticket | P3–P5 |
| `tests/sdlc/test_php_codegen.py` — layout reader, runner with/without Composer, scaffold, refusal | P1–P2 |
| `docs/specs/php-codegen-roadmap.md` (this file) | now |

**Modified**

| File | Change | Phase |
|---|---|---|
| `src/orchestrator/sdlc/testenv.py` | `PhpToolEnvironment`, `php_toolchain_available`, factory branches, `__all__` | P1 |
| `src/orchestrator/sdlc/testrunner.py` | `PhpUnitTestRunner` | P1 |
| `src/orchestrator/sdlc/feature_runner.py` | `"php"` in `SUPPORTED_LANGUAGES` and `_resolve_language`; the toolchain refusal | **P2** (not P1) |
| `src/orchestrator/sdlc/layout.py` | `"php": "php"` in the suffix map; `_resolve_php_layout`; `detect_php_layout`; `test_suffix` | P2 |
| `src/orchestrator/sdlc/scaffold.py` | `_php_files` | P2 |
| `src/orchestrator/sdlc/preflight.py` | `php -l` for changed `.php` | P2 |
| `src/orchestrator/sdlc/codegen.py` | three PHP prompt variants + layout paragraph | P3 |
| `src/orchestrator/catalog/catalog.py` | `php-conventions` | P3 |
| `.github/workflows/sdlc-dogfood.yml` | the aiemr row | P5 |
| `docs/specs/php-support-roadmap.md` | §9 deferred → shipped; §11 step 5 | P5 |
| `docs/specs/SPEC-INDEX.md`, `STATE-OF-SPINE.md` | this row; spec count `87` → `88` (gated); test counts | now |
| `README.md`, `FEATURES.md`, `USER_GUIDE.md`, `CHANGELOG.md` | PHP moves from "reads" to "reads and builds" | P5 |

**Runner:** the caller explicitly selects PHP 8.3 and Composer with pinned
`shivammathur/setup-php`. D5's version override stays in the caller, not in Spine.

## 6. Risks and gotchas

- **Adding `"php"` to `SUPPORTED_LANGUAGES` before the runner set exists** reopens the
  silent-Python-scaffold trap. It lands in P2 with the layout, and a test asserts that
  `--language php` on a PHP repo never produces a `pyproject.toml`.
- **The target's own tests are not the exit criterion (D7).** Anyone measuring "does the
  suite pass" on aiemr will get *no* on any modern PHP and conclude the profile failed. The
  claim is narrower and stated.
- **`library/` and `contrib/` are vendored, not ignored.** aiemr's graph will carry
  third-party code as first-party until P0 decides between an ignore entry and a scoped
  `--path`. Do not add `library` to `DEFAULT_IGNORE_DIRS` casually — it is a common
  first-party name; measure against the pinned comprehension repos first (the D5 rule from
  the PHP track).
- **`.blade.php` and `.inc`.** The PHP extractor's suffix rules (its D4) decide what the
  graph sees; codegen must write `.php`, never `.inc`, even when the neighbouring code does.
- **A phar on disk inside the worktree becomes a `Doc`-adjacent artifact** — it goes in the
  workspace root, never the tree, and `_relative_paths` must not report it as written.
- **PHP 5 code on PHP 8** fails at *parse*, not at test, for `mysql_*`-free files too
  (`each()`, curly-brace string offsets). `php -l` first, always.
- **`STATE-OF-SPINE` numbers and the spec count are gated**; this file bumps the count.

## 7. Sequence

1. P0 on a clone of aiemr — one day, numbers into §1, D5 confirmed.
2. P1 + P2 as one PR off `develop`, no model, gate green.
3. P3 as its own PR: prompts, conventions, the live run on a fork, the run record linked here.
4. P4, P5, release note.


## 8. Completed execution — 2026-09-10

Branch `codex/php-codegen` was created from `develop` at `6f8f565`.
Git author/committer: `Synaptixs <noreply@synaptixs.dev>`.
The maintainer requested all phases in one implementation PR rather than the
original multi-PR sequence. Real PHP validation used official PHP 7.4/8.3 containers.

### P0 measurements

Pinned aiemr: `5ba45f6166d3d4383057d0f24c1641663a4380f1` (`rel-422`).
After repairing a PHP route-scanner recursion overflow, extraction produced
**45,973 nodes and 255,678 edges**. The **65 nested Composer manifests** have
**27,837 nodes** beneath their directories (a measured dependency-subtree proxy,
not a claim that every node there is third-party). Keep `library` and `contrib`
out of global ignores; scope this ticket's plan to `library/classes`.

| Interpreter | Classes passing lint | Classes failing lint |
|---|---:|---|
| PHP 8.3 | 28 / 31 | `NumberToText.class.php`, `PQRIXml.class.php`, `Tree.class.php` |
| PHP 7.4 | 30 / 31 | `Tree.class.php` |

The historical test fails on both modern PHPUnit versions because
`require_once('PHPUnit/Framework.php')` cannot resolve. D7 is confirmed: test the
change, not the historical suite. `NumberToText` already supports negatives;
the chosen ticket repairs PHP 8-removed curly-brace string offsets and adds
modern regression coverage. PHP 8.3 remains the default, with explicit 7.4 replay.
The iterative route traversal preserves group behavior and passes a 1,500-term regression.

### P1/P2 runner and layout

Fake-tool regressions and real PHP 8.3 integration cover Composer and verified PHAR
execution, configuration paths/symlinks, legacy directory case, custom suffixes,
renames and untracked paths, checksum integrity, missing dependencies and version pins.
Both real runner paths pass correct code and fail intentionally incorrect code;
empty, skipped and incomplete test runs fail. Dependency directories remain excluded
when a proof probe removes `.gitignore`.

### P3 brownfield and episteme

Initial run `bbfc15bbeab34a2c` passed after two test iterations, with red change-removal
proof and review approval. PHP 7.4 / PHPUnit 9.6.36 replay passed 12 tests/30 assertions.

After the maintainer's episteme clarification, `understand` generated the full aiemr
bank (**133 files, 46,348 grounded nodes**) and a focused classes bank (**83 files,
2,657 grounded nodes**). Replay `3bd08673bd5b47c9` used the focused bank through
`ORCHESTRATOR_MEMORY_BANK_DIR` plus direct PKG context: **9,944 characters** of grounding.
It passed after **three test iterations**, with red proof and semantic approval.
Independent PHP 7.4 / PHPUnit 9.6.36 replay passed **21 tests, 36 assertions**.

[Validation PR #1](https://github.com/ssmith-synaptixs/aiemr/pull/1) contains the replay's
two-file diff in `b109375`: `library/classes/NumberToText.class.php` and
`Tests/NumberToTextNegativeTest.php`. One advisory long-line finding remains in the test.
Publication was separate from safe-mode generation, keeping unrelated Jira writes disabled.
The scoped plan names more candidate files than the delivered diff; the run records
that warning, and the final paths and acceptance criteria were checked directly.

### P4 greenfield

Run **`ffe22823b724427f`** generated `App\Temperature` into an empty repository.
The Composer scaffold, source and generated PHPUnit tests passed the full delivery
pipeline after **three test iterations**, including red change-removal proof,
semantic approval and clean code review. Independent PHPUnit replay passed
**18 tests, 161 assertions**. Nine files were committed locally.

Earlier attempts exposed incomplete Composer extraction, missing reviewer context,
and phase prompts that caused scaffold recreation. Setup now retries once and refuses
a missing autoloader before generation; review includes XML, lockfiles and dotfiles;
PHP prompts distinguish production, test and repair phases. The validation spec makes
PSR-4/bootstrap requirements inspectable by the code-only judge while real execution
is enforced by the runner. It permits PHP's platform requirement while excluding
third-party runtime packages. No failed attempt is counted as a successful full run.

### P5 documentation and pipeline

The aiemr CI job builds focused episteme, produces the plan without a model, and runs
real Composer/PHAR integration tests. It passed in [PR #350](https://github.com/synaptixs/spine/pull/350).
Model-backed CI execution is dispatch-only behind `spine-build` with a cost cap.
README, FEATURES, USER_GUIDE, CLI_REFERENCE, SETUP, both agent guides, CHANGELOG,
the PHP support roadmap, spec index and state counts are updated.

Local validation: **3,533 passed, 3 skipped, 51 deselected** in an isolated checkout
without the developer `.env`, including real PHP tests. The subsequent approval-history
fix and prompt changes passed **216 build-document/codegen tests**. Ruff, mypy (`src tests`),
pre-commit including secret scan, both generated SVG checks, capability matrix, state
counts (11 gated claims), PKG accuracy (zero gated regressions), PKG verify (zero errors;
one existing phantom warning), all four unchanged SDLC shapes and Spine `understand`
passed. Documentation audit: **zero stale/missing items**. Optional E2B, OCR and Postgres
tests were skipped; `integration`/`real_llm` markers remain outside the default suite.

Two additional delivery fixes were required by the real runs: `.class.php` design
references, and plan revalidation using the selected language and the same measured
run history as CLI planning. Generated episteme is kept outside the committed diff.
