# Design + Plan: adding Perl to the PKG — comprehension, then codegen, one track

**Status:** Proposed — plan for review, no code written. **Date:** 2026-09-10 · spine v3.33.2.
**Branch:** `feat/perl-support` off `develop` at `d84e666`. **Delivery: one MR** to `develop` when
every phase in §4 is done and tested (the Kotlin track's rule, [kotlin-support-roadmap.md](kotlin-support-roadmap.md) D19).
Adds a Perl front-end to the PKG extractor as one self-contained track of five phases, the same
cadence as C#/C/C++ ([language-support-roadmap.md](language-support-roadmap.md)), SQL
([sql-support-roadmap.md](sql-support-roadmap.md)), Go ([go-support-roadmap.md](go-support-roadmap.md))
and PHP ([php-support-roadmap.md](php-support-roadmap.md)). **Codegen is in scope** (P5): the
toolchain is `cpanm --installdeps .` → `prove -l t/`, hermetic and universal. Comprehension is the
whole desirability win and ships first inside the track: when P1 lands, `understand` / `state` /
`design` / `investigate` / `localize` / `rca` / `regression` and codegen *grounding* work on a Perl
codebase; P5 makes `sdlc feature --language perl` generate, build and test.

> Perl is not in the expansion roadmap's four-language set
> ([language-expansion-roadmap.md](language-expansion-roadmap.md)); it is a demand-pulled addition
> under that document's first prioritisation criterion. The extractor *pattern* is the shipped
> tree-sitter one. **Three things are genuinely new** and are where the design effort goes:
> **the package is both the namespace and the class** (no other language collapses `Module` and
> `Type` onto one declaration — §0 D2 is the one real design decision), **inheritance is data,
> not syntax** (`@ISA`, `use parent`, `extends`, `use Mojo::Base 'X'` — five spellings of one
> edge), and **the fact that only `perl` can parse Perl** — the grammar is error-tolerant, and
> recall on legacy code will be lower than on any language shipped so far. Bound it honestly.

## Roadmap currency — the rule this document follows

Every phase row in §4 carries **Status · Started · Finished · Evidence**, updated in the same
commit as the work. A phase is DONE only when its evidence column links a commit, a test name, or
a pasted command result.

---

## 0. Decisions surfaced up front

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **D1** | Parser | (a) `tree-sitter-perl` (standalone wheel), (b) `tree-sitter-language-pack`, (c) PPI via a `perl` subprocess | **(a).** Verified 2026-09-08: `tree-sitter-perl` **2.0.0** on PyPI, abi3 wheels for macOS arm64, manylinux x86/aarch64, musl, win amd64; `py>=3.8`; its only pin (`tree-sitter~=0.24`) is behind an optional `core` extra, so it coexists with the 0.26 in `uv.lock`. Probed: packages (statement **and** block form), `use`/`require`, `use parent`/`use base`, `@ISA`, Moo `has`/`extends`/`with`, every call shape in §3.2, and Perl 5.38 `class`/`method`, all with `has_error == False`. (b) is a 20+ MB dependency for one grammar; (c) needs a CPAN install on every machine. |
| **D2** | What a `package` is in the vocabulary | (a) `Module` = **file** (path-keyed, `perl:lib/Shop/Cart.pm`), `Type` = **every package** (`perl:Shop.Cart`), subs `perl:Shop.Cart.total`; (b) `Module` = package, no `Type` nodes; (c) `Module` = package and `Type` = package with a parallel id | **(a).** A Perl package is the only type unit the language has — `bless` makes any package a class. Under (b) `state` reports 0 components and `IMPLEMENTS` lands module→module. (c) invents a second id for one declaration. Under (a) the path-keyed module is the C/C++ precedent already in the corpus vocabulary table, and a `use Shop::Tax` import's placeholder `perl:Shop.Tax` is **upgraded by `FactBatch` dedup** when the grounded `Type` exists, so cross-file joins cost nothing. Functions in an implicit `main` (a script with no `package`) key on the file: `perl:bin/report.pl.usage`. |
| **D3** | Separator in ids | (a) dots: `perl:Shop.Cart.total`, (b) native `::` | **(a).** C++ keeps `::`, but its ids are bare symbols; Perl's are qualified, and the dotted form is what `import_link._DOTTED_PREFIXES`, the `docs.py` doc→symbol binder, and the registry URLs consume. One `::`→`.` conversion site. |
| **D4** | Roles (`with 'Shop::Role::Loggable'`, `Role::Tiny`, `Moose::Role`) | (a) `IMPLEMENTS` class→role, role is a `Type`, (b) skip | **(a).** Same reasoning as PHP traits: a mixin is the behavioural claim `IMPLEMENTS` already documents, and it makes blast radius reach every consumer of a role method. |
| **D5** | Which inheritance spellings resolve to `IMPLEMENTS` | (a) all five: `use parent`/`use base` lists, `our @ISA = (...)` / `push @ISA`, Moo/Moose `extends`, `use Mojo::Base 'Parent'`, 5.38 `:isa(...)`; (b) syntax-only | **(a), literal-only.** Inheritance in Perl is *data*; reading only the syntactic forms misses the majority of CPAN-era code (`@ISA` + `Exporter`). A computed `@ISA` yields nothing. |
| **D6** | `Field` in classic (hash-based) objects | (a) declared accessors only: Moo/Moose/Mojo::Base `has`, `Class::Accessor` `mk_accessors`, 5.38 `field`; (b) also infer from `$self->{key}` | **(a).** `$self->{items}` is a hash access, not a declaration; inferring fields from it fabricates a schema the author never wrote. |
| **D7** | Suffixes | (a) `.pl`, `.pm`, `.t`; (b) `.pm` only; (c) plus shebang sniffing | **(a).** `.t` files are Perl and are the tests. `.pl` collides with Prolog by name; a Prolog file parses to ERROR nodes and yields nothing, the right degradation. (c) needs a dispatcher change; document the gap. `.pod` and `.xs` are not Perl source. |
| **D8** | Invention oracle (`pkg/scope.py`) | (a) `NOT_APPLICABLE["perl"]` with a reason, (b) a `_Perl` walker | **(a).** Variables carry a sigil; `f()` and `$f->()` / `&$f` are different CST nodes. A `my $f` cannot shadow a bare call — the Java/PHP argument. |
| **D9** | Codegen in this track | (a) P5, after comprehension is proven, (b) defer | **(a).** `cpanm --installdeps .` → `prove -l t/` is a clean two-step. `"perl"` enters `SUPPORTED_LANGUAGES` **only in P5**, together with layout (`lib/` + `t/`, a `cpanfile`), scaffold, `PerlToolEnvironment`, `ProveTestRunner`, prompts and a `perl-conventions` skill — never before, so `sdlc feature --language perl` exits 2 until the runner exists rather than scaffolding Python. Proven the Go way: real `prove` green **and** red. |
| **D10** | Default `@EXPORT` and `@ISA` method resolution for bare calls | (a) a whole-repo `finalize` pass in P2, (b) never | **(a), P2, verified-only.** A bare `fmt()` after `use Shop::Util;` (no list) resolves only when the repository's own `Shop::Util` declares `fmt` in a literal `@EXPORT`; an inherited `total()` resolves only through a literal `@ISA` chain to a first-party package. Anything else is skipped — the `exporter_default` corpus case keeps it honest. |

---

## 1. Where Perl is today — nowhere

| Fact | File | Consequence |
|---|---|---|
| `.pl` / `.pm` / `.t` are not in the profiler's suffix map | [`catalog/profile.py`](../../src/orchestrator/catalog/profile.py) | A Perl repo profiles as `languages=∅` |
| `cpanfile` / `Makefile.PL` / `Build.PL` / `dist.ini` are not read as markers | `catalog/profile.py` | No framework (`mojolicious` / `catalyst` / `dancer2`) or test-runner (`prove`) detection |
| No `perl_extractor.py`, no `perl` extra, no `tree_sitter_perl` probe, no `_GRAMMAR_MODULES` entry | `pkg/`, `pyproject.toml`, `doctor.py`, `persistence.py` | **Zero graph nodes**; a warm cache would not notice the extra |
| `--language perl` is rejected (exit 2) | `feature_runner.py` | Correct until P5 (D9) |

---

## 2. Why Perl is cheaper than its reputation, and where it is not

```
CHEAP
· One grammar, prebuilt for every CI platform; parses the block-form package,
  Moo/Moose declarations, SUPER::, __PACKAGE__->, and 5.38 class syntax with no ERROR.
· Qualified names resolve DETERMINISTICALLY: `Shop::Util::fmt()` and
  `Shop::Log->new` are exact ids with no symbol table. Bare `f()` resolves to the
  same-file sub or a name in an explicit `use Foo qw(f)` import list.
· `.pl`/`.pm`/`.t` collide with no registered suffix.
· The codegen toolchain is one command each way: cpanm, prove. No build system.
NOT CHEAP
· "Only perl can parse Perl." tree-sitter is error-tolerant, so a hard file still
  yields the declarations that parse — but sub recall on legacy code WILL be below
  1.00. State it, measure it on the classic validation repo (§8), and do not tune
  labels to hide it.
· Inheritance is data (D5). Five spellings of one edge, all read literally.
· A package is both container and class (D2). One decision; get it right in P1.
```

**Reused verbatim:** lazy parser factory + `TYPE_CHECKING`-guarded `TSNode`; gated append in
`default_extractors()`; the two-pass CALLS pattern; `finalize` repoint (C#/PHP); the corpus
method; the per-front-end freshness test; the C-style path-suffix import join for `require "file.pl"`;
the Go-shaped codegen pair (`ToolEnvironment` + build-then-test runner, green and red proven).

---

## 3. Design

### 3.1 Fact mapping (P1)

| Perl construct (CST node) | Fact | Notes |
|---|---|---|
| file | `Module` `perl:lib/Shop/Cart.pm` | Path-keyed (D2) |
| `package_statement` (statement form — scopes to the next `package` or EOF; block form — its block) | `Type` `perl:Shop.Cart` | Every package (D2). A file with several packages yields several `Type`s under one `Module` |
| `use_statement` with a `package` child | `IMPORTS` module→`perl:Shop.Tax` | Placeholder, upgraded by dedup when first-party. The `qw()` list feeds the resolver. Pragmas (`strict`, `warnings`, `utf8`, `feature`, `v5.36`) skipped by a lowercase-initial rule |
| `require_expression` with a `bareword` / a string literal | `IMPORTS` | Bareword → package; `require "lib/x.pl"` → path-keyed `perl:lib/x.pl`, joined by path suffix (the C rule). Computed → nothing |
| `use parent` / `use base` / `our @ISA = (...)` / `push @ISA` / `extends` / `use Mojo::Base 'X'` / `class Foo :isa(X)` | `IMPLEMENTS` type→`perl:X` | D5, literal-only |
| `with 'Role'` / `with qw(A B)` | `IMPLEMENTS` type→role | D4 |
| `subroutine_declaration_statement`; 5.38 `method_declaration_statement` | `Function` `perl:Shop.Cart.total` | Owner = the package in scope at that line; implicit `main` → `perl:<path>.total`. Anonymous subs → no node |
| `has name => (...)` / `has [qw(a b)]` · `__PACKAGE__->mk_accessors(qw(a b))` · 5.38 `field $x` | `Field` `perl:Shop.Cart.items` | D6; sigil stripped. The `Field` only — the CALLS pass resolves `$self->items` to it |
| `CONTAINS` | module→type, module→sub (main), type→sub/field | As every front-end |
| `our $VERSION`, `@EXPORT`, `@EXPORT_OK`, file-scoped `my`, POD, `__END__`/`__DATA__` | — | `@EXPORT` is read by D10's pass, not emitted |

`is_public`: a leading underscore is Perl's convention — add `"perl"` to `_UNDERSCORE_LANGS` in
`insights.py`.

### 3.2 CALLS (P2) — precision-first

| Call shape | Resolution | Emit |
|---|---|---|
| `$self->m()` / `$class->m()` / `__PACKAGE__->m` / `shift->m` | sibling sub **or `has` field** of the enclosing package | `CALLS` → `perl:Shop.Cart.m` |
| `$self->SUPER::m()` | the first parent from D5 that resolved | `CALLS` → `perl:Parent.m`; skip when unresolved |
| `Shop::Log->new` | qualified name | `CALLS` → the sub when declared; otherwise → the `Type` (instantiation is a call to the type) |
| `Shop::Util::fmt(...)` | qualified name | `CALLS` → `perl:Shop.Util.fmt` — exact id, placeholder if third-party |
| `f()` bare | same-package sub in this file, else a name in a `use X qw(... f ...)` list, else (D10) a literal `@EXPORT` of a first-party package imported without a list, else an `@ISA`-inherited first-party sub | `CALLS` → the sub; **otherwise skip** — never a guess |
| `&f` / `&$code` / `$self->$m()` / `$obj->can('m')->()` / `goto &f` / string `eval` / `AUTOLOAD` | — | never — fabrication |
| `$obj->m()` where `$obj` was assigned a literal constructor in the same sub (`my $log = Shop::Log->new`) | receiver type from the assignment | **P3** — the typed-receiver rule, file-local |

### 3.3 Framework edges (P3)

| Framework | Source shape | Fact |
|---|---|---|
| Mojolicious (full app) | `$r->get('/orders')->to('orders#index')` · `->to(controller => 'Orders', action => 'index')` · `$r->under('/api')` groups | `Endpoint` + `EXPOSES` → `perl:App.Controller.Orders.index`. `any` → nothing (no `ANY` verb); computed paths → nothing |
| Mojolicious::Lite / Dancer2 | `get '/x' => sub {…}` | `Endpoint`, no `EXPOSES` (closure rule); `get '/x' => \&handler` → `EXPOSES` |
| Catalyst | `sub index :Path('/x') :Args(0)` | verb-less → nothing (D2 of [endpoints-typescript-go.md](endpoints-typescript-go.md)) |

### 3.4 Data layer (P4)

| Framework | Source shape | Fact |
|---|---|---|
| DBIx::Class | `__PACKAGE__->table('orders')`; `belongs_to` / `has_many` / `might_have`; `add_columns(...)` | `Entity` `perl:entity:App.Schema.Result.Order` + `REFERENCES`; columns → `Field`s on the entity |
| Rose::DB::Object / Class::DBI | `__PACKAGE__->meta->setup(...)` | Same shape, second reader |
| DBI raw SQL strings | — | a cross-language pass, its own spec |

### 3.5 Codegen (P5)

| Piece | What | Precision / safety rule |
|---|---|---|
| Layout | greenfield: `lib/<Dist>/`, `t/`, `cpanfile`, `Makefile.PL` (EUMM) or `dist.ini` absent by default; brownfield: place into the existing `lib/` tree by package name, `t/` beside it | the placement package is read from the neighbouring `.pm` files' `package` lines, never derived from the directory name alone (the Go 4.5 lesson) |
| `PerlToolEnvironment` | `cpanm --installdeps . --notest` when a `cpanfile` exists (best effort, offline-tolerant); `perl` and `prove` on PATH | `perl_toolchain_available()` = `perl` **and** `prove`; `cpanm` optional and said so |
| `ProveTestRunner` | `perl -c` on each changed file, then `prove -l t/` (or `prove -l <file>` for the test the change targets, with a whole-suite run before green) | early-return on the first non-zero, `_clip`-ed output as the refine signal |
| Prompts + skill | `perl-conventions` (strict/warnings, `Test::More`, Moo when the repo uses it, else classic `bless`) | the convention is read from the repo's own `.pm` files, not assumed |
| `SUPPORTED_LANGUAGES`, `_resolve_language` | `"perl"` added in P5 only; `auto` resolves to Perl when `.pm`/`.pl` are present and Python is not | |

---

## 4. Phases — the living table

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **P1 Comprehension** | `perl_extractor.py` (§3.1, D2–D7); package-scope tracking; `finalize` repoint; registry: `default_extractors`, `FRONT_ENDS`, `EXTRA_PROBES`, `_GRAMMAR_MODULES`, profiler markers/needles, `scope.NOT_APPLICABLE` (D8), `_DOTTED_PREFIXES`, `insights._UNDERSCORE_LANGS`, `docs.py`/`doc_link.py` `pl`/`pm`; packaging (`perl` extra, `languages` meta-extra, mypy override, `ci.yml`); tests per [docs/reviewing/language-frontend-checklist.md](../reviewing/language-frontend-checklist.md); docs per [docs/reviewing/docs-matrix.md](../reviewing/docs-matrix.md) | ~4–6 d | `pkg extract` on both validation repos yields Module/Type/Function/Field + IMPORTS/CONTAINS/IMPLEMENTS from 0; `pkg verify` 0 errors; **sub recall on the classic repo measured** against `grep -c '^sub '` and written here; `scripts/docs_audit.py` clean; gate green with `--extra perl` | ⬜ | | | |
| **P2 Corpus + CALLS** | `corpus/perl/{plain,instance_calls,exporter_default,isa_spellings,legacy_main,multi_package}` labelled from source first; §3.2 rows 1–6; D10's `finalize` pass; `--scoreboard`; freshness test | ~4–5 d | precision 1.00 on every kind; CALLS recall stated with predicted `known_gaps`; invention `NOT_APPLICABLE` with reason; `state` "Call graph: available" | ⬜ | | | |
| **P3 Routes + typed receivers** | `perl_routes.py` (Mojolicious full + Lite, Dancer2); §3.2 row 7; `corpus/perl/mojo_routes` | ~3–5 d | `Endpoint`s on the Mojolicious repo with `EXPOSES` to real controller subs; a Perl service joins as a provider in `pkg joins` | ⬜ | | | |
| **P4 Data layer** | DBIx::Class `Entity`/`REFERENCES`/`Field` (§3.4); `corpus/perl/dbic` | ~2–4 d | entities linked; `data_layer_link` reconciles against a `.sql` schema; zero invented `REFERENCES` | ⬜ | | | |
| **P5 Codegen** | §3.5: `sdlc/perl.py` layout + scaffold, `PerlToolEnvironment`, `ProveTestRunner`, prompts + `perl-conventions`, `"perl"` into `SUPPORTED_LANGUAGES`, preflight `perl -c` | ~4–6 d | greenfield `sdlc feature --language perl` → real `prove` green **and** red proven in `tests/sdlc/test_perl_integration.py` (gated on `perl_toolchain_available()`); brownfield into the Mojolicious validation repo green, independently re-run | ⬜ | | | |
| **P6 Review + MR** | `/review-pr` on the branch; fix; one MR to `develop` with this table and every validation number as its body | ~1 d | verdict "mergeable"; every §7.1 row updated; CI green; no `episteme/` in the diff | ⬜ | | | |

All phases land on `feat/perl-support`; delivery is **one MR**. **Rough total: ~18–27 days**, one
engineer familiar with the PKG. No net-new *algorithm*; P1 is a day longer than PHP's because of
package-scope tracking and the five inheritance spellings.

---

## 5. Corpus cases (P2–P4)

Vocabulary row for `corpus/README.md`: `perl` · module `perl:lib/Shop/Cart.pm` *(a path)* · type
`perl:Shop.Cart` · separator `.`.

| Case | Exercises | The finding it is built to catch |
|---|---|---|
| `plain` | one `.pm` with `package`, `use parent`, `use X qw(f)`, three subs, `$self->` and qualified calls | the control |
| `instance_calls` | `my $log = Shop::Log->new; $log->write` and `$thing->write` on a parameter | P2 skips both; P3 resolves the first only |
| `exporter_default` | `use Shop::Util;` whose literal `@EXPORT` supplies `fmt`, then a bare `fmt()`; `croak()` from `Carp` | the first-party edge is emitted by D10's pass; the `Carp` edge is invented and must be absent |
| `isa_spellings` | five packages, one per D5 spelling, plus one computed `@ISA` | five `IMPLEMENTS`; the computed one yields nothing |
| `legacy_main` | two scripts with no `package`, both defining `usage`; `require "lib/common.pl"` | distinct path-keyed ids; the `require` joins by path suffix |
| `multi_package` | statement-form and block-form packages in one file, a sub after the second `package` | the sub belongs to the second package |
| `mojo_routes` (P3) | `->to('orders#index')`, the hash form, an `under` group, a closure, `->any` | closure → endpoint without `EXPOSES`; `any` → nothing |
| `dbic` (P4) | two Result classes, `belongs_to`/`has_many`, one relation outside the tree | `REFERENCES` between the two; the outside target stays external |

Codegen (P5) is proven the Go way, green and red against real `prove`, in
`tests/sdlc/test_perl_integration.py`, not in the corpus.

---

## 6. Files to change

**New:** `src/orchestrator/pkg/perl_extractor.py`, `perl_routes.py`; `src/orchestrator/sdlc/perl.py`,
`PerlToolEnvironment` and `ProveTestRunner` in the existing `testenv.py`/`testrunner.py`;
`tests/pkg/test_perl_extractor.py`, `test_perl_routes.py`; `tests/sdlc/test_perl_codegen.py`,
`test_perl_integration.py`; `corpus/perl/*`; this file.

**Modified (P1):** `pkg/extractor.py`, `pkg/capabilities.py`, `pkg/persistence.py`, `doctor.py`,
`catalog/profile.py`, `pkg/scope.py`, `pkg/import_link.py`, `knowledge/insights.py`, `pkg/docs.py`,
`pkg/doc_link.py`, `pyproject.toml` (+ `uv.lock` one line), `.github/workflows/ci.yml`, the registry
tests (`test_default_extractors`, `test_capabilities`, `test_verifier`, `test_scope`, `test_profile`,
`test_doctor`, `test_insights`); docs per §7.1.

**Modified (P5):** `sdlc/feature_runner.py` (`SUPPORTED_LANGUAGES`, `_resolve_language`, toolchain
guard), `layout.py`, `scaffold.py`, `codegen.py` prompts, `preflight.py`, `catalog/catalog.py` +
`skills.py` (`perl-conventions`).

**Untouched until P5:** everything under `sdlc/` (D9).

### 7.1 User-facing documentation — what changes, in which phase

Updated in the phase that makes each row true, in the same commit as the code;
`scripts/docs_audit.py` reports nothing STALE or MISSING before that commit.

| Document | What must change | Phase |
|---|---|---|
| `README.md` | every language list (intro, "Works across", "Add a language" count and next-language list); "What's new" at the release cut | P1 · release |
| `FEATURES.md` | a Perl capability row; the "all N front-ends" accuracy row count; a codegen row in P5 | P1, P5 |
| `USER_GUIDE.md` | the extras list (`[perl]`), the "Multi-language" blockquote (`.pl`/`.pm`/`.t`), the corpus-results line; toolchain passage (`perl`, `prove`, `cpanm` optional) in P5 | P1, P5 |
| `KNOWLEDGE_GRAPH.md` | node/edge matrices, language table row, "Parser coverage", a fact-mapping note on D2 (package = namespace **and** class, module = file) | P1, P4 |
| `CLAUDE_GUIDE.md`, `CODEX_GUIDE.md` | the language sentence and "N front-ends"; a Perl toolchain row in P5 | P1, P5 |
| `CLI_REFERENCE.md`, `EXAMPLE.md`, `BENCHMARK.md` | corpus-results counts | P2 |
| `SETUP.md` | the `[perl]` extra; the toolchain in P5 | P1, P5 |
| `corpus/README.md` | the id-vocabulary row (path-keyed module, dotted type) | P2 |
| `plugins/spine/skills/*/SKILL.md` | the language line | P1 |
| `docs/specs/STATE-OF-SPINE.md` | front-end count, precision row count, `CALLS` recall row with Perl's number, source-module and test counts | P1, P2 |
| `docs/specs/SPEC-INDEX.md`, `language-expansion-roadmap.md` | this spec's row, updated to the phase reached — never ahead of it | every phase |
| `CHANGELOG.md` | one entry under Unreleased per phase, in the house voice | every phase |
| `assets/spine-architecture.svg` (+ `.png`) | "across N language front-ends", re-rendered by its script | P1 |

---

## 7. Packaging

- `tree-sitter-perl` 2.0.0: `language()` is the single export. **P1 step 1: confirm the parse in
  the project venv** (`uv run --frozen`), not only in the isolated probe used for D1.
- Base install stays stdlib-only: `find_spec("tree_sitter_perl")` before a function-local import;
  `tree_sitter_perl` in `persistence._GRAMMAR_MODULES` (the cross-check test fails without it).
- One CST detail to verify in P1: 5.38 `field $x :param` parses as a `variable_declaration` with
  an `attrlist` — key on the `field` keyword token.

## 8. Validation targets (ephemeral, docs-only names)

Two poles, both public, shallow-cloned to a scratch dir, extracted on a `.git`-less copy, deleted
after; names live in `docs/` only, never in `src/` or `tests/`:

- **Mojolicious** (`mojolicious/mojo`) — modern OO Perl: `use Mojo::Base 'Parent'`, `has`, roles,
  a router, ~200 `.pm` + `.t` files. Exercises D2/D4/D5, P3 routes, `.t` as source, and P5
  brownfield codegen (`prove` is its own test runner).
- **ExifTool** (`exiftool/exiftool`) — classic CPAN Perl: `@ISA = qw(Exporter)`, hash-based
  objects, huge table-driven `.pm` files, no Moose. Exercises the `@ISA` spelling, D6, D10, and
  **parse recall on hard Perl**: record the `sub` count from `grep` beside the `Function` count
  from the graph, per file, in this document.

**Baseline to record before P1:** `pkg extract` on each yields 0 Perl nodes and the profiler
reports no language.

## 9. Out of scope — not Perl

- **XS / C extensions** (`.xs`, `.c` under a Perl dist) — C, already covered by the C front-end.
- **Extensionless scripts** (shebang-only `bin/` files) — a dispatcher change; measure demand.
- **Catalyst attribute routing** without an HTTP method — verb-less, the no-`ANY` rule.
- **Embedded-SQL strings** in DBI calls — a cross-language pass, its own spec.
- **`perlcritic` as preflight** — optional; `perl -c` is the P5 preflight because it is always present.

## 10. Risks and gotchas

- **D2 is load-bearing.** Every id keys on it; changing it after P2 invalidates the corpus.
- **Package scope is positional.** A sub emitted under the wrong package is a wrong id `pkg verify`
  cannot see; the `multi_package` case exists for this.
- **Recall below 1.00 on legacy Perl is the honest number.** Write it down; never add labels that
  agree with the extractor.
- **Pragmas look like imports.** The lowercase-initial skip is the rule; a first-party lowercase
  package name is the accepted false negative.
- **`cpanm` may be absent or offline.** The environment step is best effort and says so; `prove`
  is the gate.
- **Untracked `docs/specs/*.md` changes the spec count** and, as this document once did, can be
  lost — it is tracked on its branch from its first commit.
- **Never commit `episteme/`.**

## 11. Sequence

```
0. Decide D1–D10 (this document)               — review; D2 first, it keys everything
1. P1 comprehension  → recall on ExifTool recorded
2. P2 corpus + CALLS → corpus 1.00 precision, exporter_default green, @EXPORT/@ISA pass
3. P3 routes + typed receivers → Perl provider in the multi-repo joiner
4. P4 DBIx::Class entities
5. P5 codegen → sdlc feature --language perl, prove green + red; brownfield on Mojolicious
6. /review-pr, then one MR to develop
```
