# Design + Plan: PHP codegen — the profile the PHP track deferred

**Status:** implementation in progress on `codex/php-codegen` (2026-09-10).
Recommendations D1–D7 accepted by the maintainer. P0–P3 are verified; P4/P5 validation
are in progress; phases are marked complete only after their exit checks pass. Follow-on to
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
| **P4 Greenfield, live** | The Go 4.4 equivalent: an LLM-generated small library into an empty repo. | ~0.5 d | Composer scaffold + generated code + tests green in one run | in progress — model runs exposed setup and review gaps; corrected |
| **P5 Pipeline + docs** | `sdlc-dogfood.yml` gains an aiemr row (plan-only in CI, build on dispatch — see [the reusable workflow](../../.github/workflows/spine-sdlc.yml)); `php-support-roadmap.md` §9/§11 updated; README/FEATURES/USER_GUIDE language lists say PHP builds, not only reads; `STATE-OF-SPINE` numbers. | ~0.5 d | `python scripts/sdlc_shapes.py` unchanged; the aiemr plan job green in CI | in progress — implementation/docs and local gates pass; CI pending |

P1 and P2 can land as one PR; P3 is the one that costs tokens and is the proof.

## 5. Files to change

**New**

| File | Phase |
|---|---|
| `tests/sdlc/test_php_codegen.py` — layout reader, runner with/without Composer, scaffold, refusal | P1–P2 |
| `docs/specs/php-codegen-roadmap.md` (this file) | now |

**Modified**

| File | Change | Phase |
|---|---|---|
| `src/orchestrator/sdlc/testenv.py` | `PhpToolEnvironment`, `php_toolchain_available`, factory branches, `__all__` | P1 |
| `src/orchestrator/sdlc/testrunner.py` | `PhpUnitTestRunner` after `GoTestRunner` | P1 |
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

**Runner:** GitHub's `ubuntu-latest` image ships PHP 8.3 and Composer; nothing to add for
the default. D5's pin uses `shivammathur/setup-php` in the caller, not in Spine.

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


## 8. Execution log — 2026-09-10

- Branch: `codex/php-codegen`, based on `develop` at `6f8f565`.
- Implemented pending verification: PHP environment/factories, configuration-aware layout,
  Composer scaffold, changed-file lint and PHPUnit runner, prompts and convention samples.
- Added regression coverage for configuration paths, legacy directory case, custom suffixes,
  changed-file discovery, checksum validation, toolchain selection and empty-test refusal.
- P0 correction: `NumberToText` already supports negative numbers. Its active curly-brace
  string offsets fail PHP 8 parsing. The brownfield ticket will make that class PHP 8
  compatible and add modern PHPUnit regression coverage for existing negative-number behavior.
- Real PHP 7.4 and 8.3 validation uses official PHP container images, since this host has
  neither PHP nor Composer installed. No system-wide toolchain installation is required.

### P0 measurements

Pinned aiemr commit: `5ba45f6166d3d4383057d0f24c1641663a4380f1` (`rel-422`).
After repairing the PHP route scanner's recursion overflow, extraction produced
**45,973 nodes and 255,678 edges**. The **65 nested Composer manifests** contain
**27,837 nodes** beneath their directories (a measured dependency-subtree proxy,
not a claim that every node in those trees is third-party). Keep `library` and
`contrib` out of global ignores; scope this ticket's plan to `library/classes`.

| Interpreter | Classes passing lint | Classes failing lint |
|---|---:|---|
| PHP 8.3 | 28 / 31 | `NumberToText.class.php`, `PQRIXml.class.php`, `Tree.class.php` |
| PHP 7.4 | 30 / 31 | `Tree.class.php` |

The original `Tests/NumberToTextTest.php` fails on both modern PHPUnit versions
because `require_once('PHPUnit/Framework.php')` cannot resolve. D7 is confirmed:
run the generated tests, not the historical suite. PHP 8.3 remains the validation
default, with explicit PHP 7.4 compatibility checked for the changed class.

The route-scanner fix uses iterative expression traversal, preserving route order
and group-prefix handling. Its new 1,500-term expression regression and all route
tests pass (**11 passed**).

P1/P2 real integration: Composer scaffold and no-Composer PHAR both pass a real
assertion on PHP 8.3 and both reject an intentionally wrong implementation.

### P3 validation

Run `bbfc15bbeab34a2c` completed against the pinned aiemr checkout: implement → tests
→ refine → green → review. Exactly **two files** changed: the existing
`library/classes/NumberToText.class.php` and new `Tests/NumberToTextNegativeTest.php`.
There were **two test iterations**, and the change-removal proof was red without
the implementation. Independent PHP 7.4 / PHPUnit 9.6.36 replay passed **12 tests,
30 assertions**. PHP 8.3 / PHPUnit 11 passed through the production runner.

A separate publication step pushes this tested commit to a fork and opens its PR;
the validation run leaves tracker writes disabled. This achieves the live delivery
proof without filing an unrelated Jira ticket. [Validation PR #1](https://github.com/ssmith-synaptixs/aiemr/pull/1) is open.

The scoped plan reports more candidate files than the final change, and paths relative
to its graph root differ from checkout-relative delivery paths. The run records this
plan-fit warning; the acceptance criteria and final two-file diff were checked directly.

Additional integration fixes discovered by the real run: PHP filenames are recognized
by the design validator, PHP source enters review/test recognition, and plan approval
revalidation uses the chosen language.


P4 execution note: the first greenfield attempt hit an incomplete Composer install
on the container's shared filesystem and exhausted its refinement attempts. The
runner now retries setup once and refuses missing `vendor/autoload.php` before any
model runs. A fresh run has passed its initial tests and is completing proof/review.

Full-suite validation runs in a separate checkout without the developer `.env`:
the initial sandboxed run had filesystem/network restrictions and a pre-existing
unconfigured-Jira test read the local environment file. The isolated run removes
that confounder without changing application behavior or the user's environment.


P5 local checks so far: all four `sdlc_shapes.py` shapes pass, `pkg verify` reports
zero errors (one existing phantom-module warning), the accuracy gate has zero gated
regressions, both generated SVG checks and the capability matrix check pass.
`mypy src tests` and `ruff check` pass. The isolated full suite passes; CI remains pending.


Full isolated suite: **3,533 passed, 3 skipped, 51 deselected**. Real PHP/Composer
integration ran. Skips: E2B key absent, pytesseract absent, optional Postgres integration
not enabled; `integration` and `real_llm` markers remain outside the default suite.
Separate model-backed P3/P4 validation is recorded above/below. `understand` also built
successfully on the isolated Spine checkout; no generated `episteme/` files are staged.

Maintainer clarification: aiemr's PKG directly grounded the successful brownfield run
(**7,375 characters** of graph context). A readable aiemr `episteme/` is now being
created as well; the next validation replay will use that generated knowledge through
`ORCHESTRATOR_MEMORY_BANK_DIR` plus the direct PKG context.


P4 review correction: the second generated library passed tests and change-removal
proof, but semantic review could not see `phpunit.xml` or `composer.lock` because
its file-type allowlist omitted them. Both now reach the judge, with regression coverage.
Dependency directories are also excluded even when `.gitignore` is removed by a proof
probe. Generated tests marked skipped/incomplete fail rather than reporting success.


### Episteme and final validation

The full aiemr episteme is built: **133 files**, **46,348 grounded nodes**. A focused
`library/classes` episteme has **83 files**, **2,657 grounded nodes** and is configured
through `ORCHESTRATOR_MEMORY_BANK_DIR` for the brownfield replay. CI now generates
this focused episteme before planning or building as well. Knowledge files are local
validation artifacts, not committed source.

The generated greenfield tests repeatedly passed with a red change-removal proof.
A final validation uses a structural Composer/PSR-4/bootstrap criterion that the
code-only judge can inspect; the production runner independently enforces real test
success. Earlier attempts found missing review context for XML, lockfiles and
dotfiles; all now reach the judge.

Replaying tickets also exposed an existing approval mismatch: the CLI renders
measured run history into the plan, while revalidation omitted it. Revalidation
now uses the same history, with a regression test.


P3 episteme replay `3bd08673bd5b47c9` completed with **9,944 characters** of combined
episteme and graph grounding, **three test iterations**, passing change-removal proof
and semantic review. Independent PHP 7.4 / PHPUnit 9.6.36 replay passed **21 tests,
36 assertions**. The fork PR now carries this replay's final two-file diff in commit
`b109375`; one advisory long-line review finding remains in its generated test.

P5: [Spine merge PR #350](https://github.com/synaptixs/spine/pull/350) is open against
`develop`. Its aiemr episteme/plan job and real PHP runner tests passed in CI.
The main quality job is still running.
