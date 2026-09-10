# Design + Plan: Perl codegen — `sdlc feature --language perl`, built and tested with `prove`

**Status:** Proposed — plan for review, no code written. **Date:** 2026-09-10 · spine v3.33.2.
**Depends on:** [perl-support-roadmap.md](perl-support-roadmap.md) merged (P1 for grounding, P2 for
the call graph the generated code is grounded on). **Branch:** `feat/perl-codegen` off `develop`,
opened when that dependency lands. **Delivery: one MR** to `develop` when every phase in §3 is done
and tested. Same split as Java ([multi-language-java.md](multi-language-java.md) →
[java-codegen.md](java-codegen.md)) and TypeScript ([typescript-codegen.md](typescript-codegen.md)):
comprehension is its own track, codegen is its own track, both in scope.

> The toolchain is the whole story: `cpanm --installdeps .` then `prove -l t/`. One dependency
> installer that may be absent, one test runner that is always present with `perl`. No build
> step, no build-system detection, no TFM probing. Cheaper than C, Go or PHP; the design effort
> goes into **where generated code lands in an existing distribution** and into proving green
> **and** red against the real toolchain, the Go 4.2 / 4.5 lessons.

## Roadmap currency

Every phase row in §3 carries **Status · Started · Finished · Evidence**, updated in the same
commit as the work. DONE means the evidence column links a commit, a test, or a pasted result.

---

## 0. Decisions surfaced up front

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **C1** | Greenfield layout | (a) `lib/<Dist>/…pm` + `t/*.t` + `cpanfile`, no build tool; (b) ExtUtils::MakeMaker (`Makefile.PL`); (c) Dist::Zilla | **(a).** `prove -l t/` needs nothing else, and a `cpanfile` is what `cpanm --installdeps .` reads. (b)/(c) are packaging for CPAN release, not for building and testing; a repo that has one keeps it (brownfield never adds or removes a build tool). `tests_dir` is `t/`, `source_dir` is `lib/` — distinct, unlike Go. |
| **C2** | Test runner | `ProveTestRunner`: `perl -c` on each changed `.pm`/`.pl`, then `prove -l t/` (whole suite before green); early-return on the first non-zero; `_clip`-ed output as the refine signal | The two-step shape of `CTestRunner`/`GoTestRunner`. A brownfield run targets the `t/` directory that owns the changed package's tests first, then the whole suite, so a change cannot pass untested (the Go 4.5 false-green). |
| **C3** | Dependency install | `PerlToolEnvironment.ensure` runs `cpanm --installdeps . --notest` when a `cpanfile` exists **and** `cpanm` is on PATH; otherwise it says so and continues | `cpanm` is not core Perl. `perl_toolchain_available()` requires `perl` and `prove` only; a missing `cpanm` is a warning in the run log, never a silent pass and never a hard stop for a repo whose deps are already installed. |
| **C4** | Brownfield placement | the target package is read from the neighbouring `.pm` files' `package` lines and the `lib/` tree; the new module goes at `lib/<Package/Path>.pm` and its test at `t/<name>.t` | The Go 4.5 lesson (placement by an existing package clause, not by directory name). A repo with several `lib/` roots (monorepo of distributions) targets the one whose `cpanfile`/`Makefile.PL` is nearest the grounded landing site. |
| **C5** | Conventions | `perl-conventions` skill reads the repo: Moo/Moose when the repo `use`s it, else classic `bless`; `use strict; use warnings;` always; `Test::More` (or `Test2::V0` when present); a leading underscore for private subs; POD stub per public sub | Read, not assumed — the PHP conventions precedent. |
| **C6** | Preflight | `perl -c` per changed file, always; `perlcritic` only when a `.perlcriticrc` exists | `perl -c` is present wherever `perl` is; `perlcritic` is a CPAN install. Fills the Python-only preflight gap for Perl the way Go's `gofmt`/`go vet` was proposed to. |
| **C7** | `SUPPORTED_LANGUAGES` | `"perl"` added in the **first** commit that also adds layout, scaffold, environment and runner — never earlier | The silent-Python-scaffold trap the Go track closed: `--language perl` exits 2 until the whole set exists. `_resolve_language auto` → Perl when `.pm`/`.pl` are present and Python is not. |
| **C8** | Live proof | greenfield with a real model against a spec; brownfield into the Mojolicious validation repo | Both independently re-run (`prove` from a clean checkout) before a phase is DONE — the Go 4.4 false-green is the precedent this rule exists for. |

---

## 1. What is reused

| Built in… | Reused here |
|---|---|
| codegen language-branch pattern (C#, Go, PHP) | verbatim: layout / scaffold / testenv / testrunner / prompts / conventions |
| build-then-test runner shape (`CTestRunner`, `GoTestRunner`) | `perl -c` → `prove` |
| module-owning test targeting (Go 4.5) | the `t/` directory nearest the changed package |
| `--language` validation (Go) | `"perl"` enters the set with the machinery, not before |
| PKG grounding (`grounding.py`) | the Perl graph from the support track; the grounding fence language is Perl |

## 2. Design

| Piece | What |
|---|---|
| `sdlc/layout.py` | `_SOURCE_EXT["perl"] = "pm"`; `detect_perl_layout` (existing `lib/` + `t/`, `cpanfile`/`Makefile.PL`/`Build.PL`/`dist.ini` as markers); `_resolve_perl_layout` — greenfield = `lib/` + `t/` + `cpanfile` (C1) |
| `sdlc/scaffold.py` | `_perl_files`: `cpanfile`, a stub `lib/<Dist>.pm` (`package`, `use strict; use warnings;`, `1;`), `t/00-load.t` (`use_ok`), README, `.gitignore` — an empty distribution is a green `prove` |
| `sdlc/testenv.py` | `PerlToolEnvironment` (C3); `perl_toolchain_available()` |
| `sdlc/testrunner.py` | `ProveTestRunner` (C2) |
| `sdlc/codegen.py` | Perl variants of the implement / tests / refine prompts; a `layout.language == "perl"` guidance block (package name, `t/` naming, Moo vs classic per C5) |
| `catalog/catalog.py`, `skills.py` | `perl-conventions` capability + skill |
| `sdlc/preflight.py` | the Perl branch of the preflight dispatcher (C6) |
| `sdlc/feature_runner.py` | `SUPPORTED_LANGUAGES`, `_resolve_language`, the toolchain guard with a `FeatureRunError` hint naming `perl` and `prove` |

---

## 3. Phases — the living table

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **C-1 Machinery** | §2 in full; unit tests: `test_scaffold_perl_*` (+ idempotency), `test_perl_toolchain_available` (monkeypatched `which`), layout detection, runner argv, the `FeatureRunError` hint; `tests/sdlc/test_perl_integration.py` gated on `perl_toolchain_available()` — scaffold → real `prove` **green and red** | ~3–4 d | integration test green and red against real `perl`/`prove`; `--language perl` validated; gate green | ⬜ | | | |
| **C-2 Greenfield live-proven** | `sdlc feature --language perl` from a spec with a real model, `--safe`; `perl-conventions` selected; grounded on the Perl graph | ~1–2 d | `prove` green, independently re-run from a clean checkout; the run's build document names the grounding used | ⬜ | | | |
| **C-3 Brownfield on the Mojolicious validation repo** | placement per C4 into an existing `lib/` tree; the owning `t/` targeted first, then the suite | ~2–3 d | `prove` green on the changed package **and** the whole suite, independently re-run; no package clause mismatch; grounding measured (chars of PKG context) | ⬜ | | | |
| **C-4 Preflight + docs + MR** | C6; every row of §5; `/review-pr`; one MR to `develop` | ~1–2 d | preflight runs `perl -c` on a changed file and fails on a syntax error (tested); docs audit clean; verdict "mergeable" | ⬜ | | | |

**Rough total: ~7–11 days.** Delivery is one MR.

---

## 4. Validation

- **Greenfield:** a small spec (a `Shop::Cart` distribution with `subtotal`/`total` and a tax
  rate) — the same shape the corpus `plain` case uses, so the generated code is checkable against
  the graph the support track already labelled.
- **Brownfield:** `mojolicious/mojo` (ephemeral, docs-only name): add a helper to an existing
  package under `lib/Mojo/` with a `t/` test; `prove -l t/mojo/<name>.t` then `prove -l t/`.
  Hypothesis to confirm, the Go-style one: the Mojolicious suite is hermetic and green from a
  clean clone in under a minute, so the brownfield loop needs no environmental wall.

## 5. User-facing documentation — updated in the phase that makes each row true

| Document | What must change | Phase |
|---|---|---|
| `FEATURES.md` | a Perl codegen row (`sdlc feature --language perl`; `prove`; `cpanm` optional) | C-1 |
| `USER_GUIDE.md` | the toolchain passage (Perl codegen needs `perl` and `prove`; `cpanm` optional); the "Multi-language" blockquote's codegen sentence | C-1 |
| `CLAUDE_GUIDE.md`, `CODEX_GUIDE.md` | a Perl row in the toolchain tables | C-1 |
| `SETUP.md` | toolchain prerequisites | C-1 |
| `CLI_REFERENCE.md` | `--language perl` in the `sdlc feature` reference | C-1 |
| `docs/specs/STATE-OF-SPINE.md`, `SPEC-INDEX.md`, [perl-support-roadmap.md](perl-support-roadmap.md) D9 | status lines updated to the phase reached | every phase |
| `CHANGELOG.md` | one entry under Unreleased per phase | every phase |

## 6. Risks and gotchas

- **`cpanm` absent or offline** — best effort, logged, never a silent pass (C3).
- **`@INC` and `-l`** — `prove -l` adds `lib/`; a repo with `blib/` or a custom `-I` in its
  test harness needs `PERL5LIB` read from `.proverc`/`dist.ini` — read it, do not guess.
- **Test naming** — `t/` files are numbered by convention (`00-load.t`, `10-cart.t`); generated
  tests follow the repo's existing numbering or, greenfield, start at `00-load.t`.
- **Never an XS build** — a distribution with `.xs` sources is compiled by `make`, which this
  track does not run; the run says so and stops.
- **Never commit `episteme/`;** the validation repository's name stays in `docs/`.

## 7. Sequence

```
depends on perl-support-roadmap.md merged (P1 + P2 at least)
C-1 machinery        → prove green + red proven against real perl
C-2 greenfield       → live-proven, independently re-run
C-3 brownfield       → into the Mojolicious repo, owning t/ then the suite
C-4 preflight + docs → /review-pr, then one MR to develop
```
