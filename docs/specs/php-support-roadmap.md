# Design + Plan: adding PHP to the PKG (9th language) — comprehension first

**Status:** ✅ **All four phases DONE** on branch `feat/php-support` — P1 (comprehension), P2
(corpus + CALLS), P3 (Laravel/Slim/Symfony routes + typed receivers), and P4
(Eloquent/Doctrine entities). See D0-D8 below for the decisions as implemented; only **codegen**
(explicitly deferred, D6/§9) remains. **Date:** 2026-09-08 · spine v3.32.0.
Adds a PHP front-end to the PKG extractor as one self-contained track of four phases, the same
cadence as C#/C/C++ ([language-support-roadmap.md](language-support-roadmap.md)), SQL
([sql-support-roadmap.md](sql-support-roadmap.md)) and Go
([go-support-roadmap.md](go-support-roadmap.md)). **Codegen is deliberately out of this track**
(§9) — the expansion roadmap's rule is comprehension first, and comprehension is where the whole
desirability win lands: the moment P1 ships, `understand` / `state` / `design` / `investigate` /
`localize` / `rca` / `regression` and codegen *grounding* work on a PHP codebase.

> This takes the fourth expansion slot that
> [language-expansion-roadmap.md](language-expansion-roadmap.md) left open ("Ruby vs. PHP"). The
> extractor *pattern* is the shipped tree-sitter one — a `LanguageExtractor` mapping a CST onto the
> universal `facts` vocabulary, template `csharp_extractor.py` (namespace-keyed modules, the
> closest analogue). Three things are genuinely new and are where the design effort goes:
> **traits** (a mixin with no vocabulary precedent), **deterministic class-name resolution**
> (PHP's namespace rules make a file-local resolver exact, without a symbol table — the
> opposite of the C#/Go walls), and **a suffix trap** (`.blade.php` templates share `.php`).

---

## 0. Decisions surfaced up front

Each is a judgement call that shapes the code. Recommendation first; the rest of the document
assumes it.

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **D0** | Take the 4th expansion slot with PHP rather than Ruby | (a) PHP, (b) Ruby, (c) both later | **(a).** Larger install base (Laravel / Symfony / WordPress), and PHP's class-name resolution is *static* (§3.2), so `CALLS` is tractable — Ruby's is not. Ruby stays queued. **Done:** [language-expansion-roadmap.md](language-expansion-roadmap.md)'s open question 1 resolved PHP over Ruby on 2026-09-08. |
| **D1** | Which grammar in the `tree-sitter-php` wheel | (a) `language_php_only` — pure PHP, (b) `language_php` — HTML host with PHP injections | **(a).** Source files open with `<?php` and never leave it; the HTML grammar adds a `text` host layer every node sits under. Verified 2026-09-08: `language_php_only` parses namespaces, group `use`, attributes, promoted ctor params, traits, enums and every call shape in §3.2 with `has_error == False`. Mixed HTML files parse with ERROR nodes and still yield their declarations (tree-sitter never raises). |
| **D2** | Id form for namespaced symbols | (a) dots: `php:App.Http.Controllers.OrderController.index`, (b) native backslashes `php:App\Http\...` | **(a).** The backslash form breaks the doc→symbol binder (dotted identifiers only — a README naming `App\Models\Order` would never bind) and every URL that carries an id in the registry UI. Convert `\`→`.` at the boundary, once, in `module_name`/`_qualify`. C# and Java already key this way. **Correction, found by the P2 corpus, not by inspection:** *not* `import_link._DOTTED_PREFIXES`. That prefix-walk exists for languages with an `__init__`-style re-export (Python's `from click import echo`); PHP has none, so the "longest dotted prefix that is a first-party module" rule falsely matched the *importing file's own module* against an unrelated import sharing its namespace as a text prefix (`namespace App\Svc` swallowing its own `use App\Svc\Support\Formatter` — the `plain` corpus case's `IMPORTS` edge landed on `php:App.Svc` instead of the real target). PHP's `use` targets are the *exact, complete* id already (the PHP RFC's own resolution rule), so an in-repo target joins via the `FactBatch` dedup alone — no prefix walk needed or wanted. PHP was left out of `_DOTTED_PREFIXES` entirely; see D7 for the one shape that does need a custom matcher. |
| **D3** | What a trait `use Loggable;` inside a class becomes | (a) `IMPLEMENTS` class→trait, trait is a `Type`, (b) `CONTAINS`, (c) skip | **(a).** `IMPLEMENTS` is documented as "subclass / interface impl" — a mixin is the same *behavioural* claim, and it is what makes blast radius right: editing a trait method must reach every class using it. (b) is a category error (the class does not own the trait); (c) hides the one construct PHP-specific reviewers will look for first. Closed enum, no new kind. |
| **D4** | `.blade.php` and other templates | (a) skip by *filename* suffix `.blade.php`, (b) parse them, (c) drop `.php` files containing HTML | **(a).** `Path.suffix` of `x.blade.php` is `.php`, so the dispatcher will hand every Laravel view to the front-end. Blade is a template language; parsing it yields ERROR-heavy trees and phantom `Module` nodes named for views. A filename rule inside `PhpExtractor.extract` (return an empty batch) keeps the dispatcher language-agnostic. `.phtml` / `.twig` are not registered suffixes and need nothing. |
| **D5** | `vendor/` (Composer's `node_modules`) | (a) add `"vendor"` to `DEFAULT_IGNORE_DIRS`, (b) PHP-only skip in the front-end, (c) nothing | **(a), with one check first.** A Laravel app carries ~10k vendored `.php` files; ingesting them presents Symfony as part of the repo (the `corpus/` phantom-node trap at scale). `vendor/` is also Go's vendoring dir — the Go roadmap flagged the same bloat and never fixed it. Risk: the `comprehension` gate is a *ratchet* on anchored facts over the five pinned repos; if any pinned tree has a `vendor/`, the count drops and `--check --pinned-corpus` fails. **P1 step 0, done:** shallow-cloned all five `evals/comprehension_corpus.yaml` repos at their pinned SHAs (vue-core, gin, fmt, libuv, flask) and grepped for a `vendor/` directory — none has one, so `"vendor"` was added unconditionally, no rebaseline needed. |
| **D6** | Codegen (composer + PHPUnit) in this track | (a) defer to a follow-on spec, (b) include as P5 | **(a).** The toolchain is clean (`composer install` → `vendor/bin/phpunit`, hermetic) so it is *affordable* later — but the expansion roadmap's strategy is comprehension first, and adding `"php"` to `SUPPORTED_LANGUAGES` without the layout/scaffold/runner set re-opens the silent-Python-scaffold trap the Go track closed. `sdlc feature --language php` keeps exiting 2 until the follow-on lands. |
| **D7** | `require` / `include` of a string literal | (a) emit `IMPORTS` to a path-keyed module, literal paths only, (b) skip | **(a).** Non-namespaced PHP (WordPress, legacy apps) has no `use`; `require_once __DIR__ . '/inc/x.php'` *is* its import graph. Literal-only, resolved relative to the importing file, joined by path suffix the way C's `#include` is. **As built:** a new `_match_php_path` in `import_link.py`, not a reuse of `_match_c` itself (different prefix, own candidate list keyed off `.php`-suffixed module bodies) — same technique, small enough not to share code, dispatched ahead of the (now php-free) `_DOTTED_PREFIXES` branch by checking the target body's shape (`.php`-suffixed = a D7 path; anything else = handled by dedup alone, see D2). Computed paths yield nothing — precision-first. |
| **D8** | Invention oracle (`pkg/scope.py`) | (a) `NOT_APPLICABLE["php"]` with a reason, (b) write a `_Php` walker | **(a).** PHP variables carry a `$` sigil; `f()` and `$f()` are different CST nodes (`function_call_expression` whose `function` child is a `name` vs a `variable_name`). A local cannot shadow a bare call, exactly as Java's separate namespaces make it not-applicable. The reason string is the deliverable — "0" and "not measured" are the two readings this project keeps confusing. |

---

## 1. Where PHP is today — nowhere, and safely so

| Fact | File | Consequence |
|---|---|---|
| `.php` is **not** in the profiler's suffix map | [`catalog/profile.py:19`](../../src/orchestrator/catalog/profile.py) | A PHP repo profiles as `languages=∅`; `state` says "unknown" |
| `composer.json` is not read as a marker | `catalog/profile.py:107` | No framework (`laravel`/`symfony`) or test-runner (`phpunit`/`pest`) detection |
| No `php_extractor.py`, no `php` extra, no `tree_sitter_php` probe | `pkg/`, `pyproject.toml`, `doctor.py:147` | **Zero graph nodes**; `doctor` cannot say the extra is missing |
| `--language php` is rejected (exit 2) | `feature_runner.py:582` | Correct — the Go track's validation holds; keep it that way (D6) |

Unlike Go, PHP is not "half-wired" — nothing to unpick, one new front-end plus registry entries.

---

## 2. Why PHP is cheaper than it looks

```
· ONE grammar, prebuilt wheels for every platform CI runs (tree-sitter-php 0.24.1:
  macOS x86/arm, manylinux x86/aarch64, musl, win amd64/arm64; py>=3.10, abi3).
· Modules key on the namespace — the C# shape, already shipped, incl. the "no
  namespace → repo-relative path" fallback and the finalize() repoint of guessed bases.
· Class-name resolution is DETERMINISTIC per file (PHP RFC: fully-qualified,
  qualified, unqualified → current namespace unless `use`d). So `new Order()`,
  `Pricing::rate()`, and a typed `$this->pricing->total()` resolve to an exact id
  with no repo-wide table — FactBatch dedup upgrades the placeholder when the
  grounded node exists. This is better than C# (intra-type only) and Go (file-local).
· `.php` collides with no existing suffix.
The cost is NOT parsing. It is three rules: traits (D3), the template trap (D4),
and functions' global fallback (§3.2 — skip, don't guess).
```

**Reused verbatim:** lazy parser factory + `TYPE_CHECKING`-guarded `TSNode`; gated append in
`default_extractors()`; the two-pass CALLS pattern (collect bodies → resolve callees); `finalize`
hook for cross-file repoints; corpus method (`corpus/README.md`); the per-front-end freshness
test in `test_verifier.py`.

---

## 3. Design

### 3.1 Fact mapping (P1)

| PHP construct (CST node) | Fact | Notes |
|---|---|---|
| file; `namespace_definition` | `Module` `php:App.Http.Controllers` | Namespace-keyed (D2); one node per namespace across files, as C# partial classes collapse. No namespace → `php:<repo-relative path>` (`rel_module_name`) |
| `namespace_use_declaration` (+ `namespace_use_group`, alias) | `IMPORTS` module→`php:<FQN>` | External placeholder; `import_link` joins the dotted prefix to a first-party module. `use function` / `use const` likewise; the alias feeds the resolver table only |
| `require`/`include` string literal | `IMPORTS` module→`php:<path>` | D7 — literal only |
| `class_declaration` / `interface_declaration` / `trait_declaration` / `enum_declaration` | `Type` `php:NS.Name` | Abstract/final/readonly modifiers ignored; anonymous classes skipped (no name) |
| `base_clause` (class `extends`, interface `extends`) | `IMPLEMENTS` type→base | Resolved by the namespace rule; unresolvable → external placeholder repointed in `finalize` (C# precedent) |
| `class_interface_clause` (`implements A, B`) | `IMPLEMENTS` type→interface | |
| `use_declaration` inside a class body (trait use) | `IMPLEMENTS` type→trait | D3. Conflict-resolution blocks (`insteadof`/`as`) ignored |
| `function_definition` (top-level) | `Function` `php:NS.name` | Namespaced free function |
| `method_declaration` (class/trait/interface/enum) | `Function` `php:NS.Type.name` | Interface method signatures emitted too (as Go does) — the target for future method-set reasoning and for `IMPLEMENTS`-aware blast radius |
| `property_declaration` → `property_element` | `Field` `php:NS.Type.name` | `$` stripped from the id, kept out of `name` too (`count`, not `$count`) — consistent with what a reader types |
| `property_promotion_parameter` (ctor) | `Field` | PHP 8 promoted properties — the most common Laravel/Symfony shape; also seeds the typed-receiver table (§3.2) |
| `const_declaration` in a type; `enum_case` | `Field` | Type-owned constants only; a top-level `const`/`define()` is not a `Field` (corpus rule: a `Field` belongs to a `Type`) |
| `CONTAINS` | module→type, module→function, type→method/field | Same as every front-end |
| closures / arrow fns / anonymous classes / `#[Attribute]` classes | — | No name → no node; attributes read only where a framework pass (P3) asks for one |

Language tag on every node: `language="php"`. `is_public` returns `None` for PHP, as it does for
Java and C# (keyword visibility the graph does not record) — no change to `insights.py`; note it
in the docstring rather than invent an underscore rule.

### 3.2 CALLS (P2) — precision-first, and where PHP is unusually kind

Two passes per file: collect a **resolver table** (the `use` map, current namespace, this file's
declared functions/types, and — per class — the typed properties and promoted parameters), then
resolve each call site.

| Call shape | Resolution | Emit |
|---|---|---|
| `$this->m()` | sibling method of the enclosing type | `CALLS` → `php:NS.T.m` (C# rule). **As built:** "sibling method" includes a same-file `use`d trait's method when the type doesn't override it (D3's `traits` corpus case) — an override always wins over the trait's own. Cross-file trait flattening is a known gap (needs the whole-repo knowledge `finalize` has, which this per-file pass doesn't) |
| `self::m()` / `static::m()` | sibling static method | same (including the trait-flattening rule above) |
| `parent::m()` | the base class from `base_clause`, if resolvable | `CALLS` → `php:NS.Base.m` — exact id by the namespace rule; skip when the base is an unresolved import. **As built:** a *method*-shaped target has no `finalize` backstop (unlike `new X()`'s bare `Type` id — chopping `NS.Base.m` to its last segment would wrongly discard the class name, not just the guessed namespace), so "resolvable" means **verified**: an explicit `use`/fully-qualified name, or a same-namespace guess that matches a type declared in *this file*. An unverified guess is skipped outright — recall loss, zero invention, the same trade-off row 5 makes |
| `new X(...)` | X by the namespace rule (`use` map → current namespace) | `CALLS` → the `Type` node (corpus rule: instantiation is a call to the type). A same-namespace guess that turns out wrong is repointed by `finalize`, exactly like an `IMPLEMENTS` guess (same bare-`Type`-id shape). **Fixed after review of #334 (2026-09-08):** `new self()` / `new static()` are the enclosing class and `new parent()` its verified base — they had been resolved as classes *named* `self`/`static`, which `finalize` then materialised as one phantom external `Type` per name; a method named by a variable (`X::$m()`, `$obj->$m()`) is skipped, and the walk no longer descends into an anonymous class (its `$this` is not the enclosing class's) |
| `X::m()` | X by the namespace rule | `CALLS` → `php:NS.X.m` — placeholder if X is third-party (Laravel facades land here as external and stay external, honestly). Same **verified**-only rule as `parent::m()`, for the same reason |
| `f()` bare | **same-file** function, else a `use function` import | PHP falls back to the *global* namespace for functions — a bare `helper()` in `App\Svc` may be `App\Svc\helper` **or** `\helper`. Same-file/imported only; otherwise **skip** (do not guess the global). **As built, one addition:** `\helper()` (a *leading*-backslash, fully-qualified call) is resolved unconditionally — unlike the bare form, PHP syntax makes it unambiguous, so it is a read fact, not a guess |
| `$this->prop->m()` where `prop` is a typed property / promoted param | receiver type from the class's own declarations | `CALLS` → `php:NS.PropType.m` — the TypeScript 3.27 typed-receiver rule, file-local. **P3**, not P2, so P2 shipped a number the corpus could pin first. ✅ **Done** — same **verified**-only rule as `X::m()`/`parent::m()` (no `finalize` backstop for a method id) |
| `$obj->m()` on a parameter with a type hint | receiver type from the signature | P3 with the row above. ✅ **Done**, same rule; an untyped parameter has no declared type and stays a permanent, honest miss (the `instance_calls` corpus case) |
| `$obj->$name()`, `call_user_func`, `__call`, `array_map('fn', …)`, string callables | — | never — fabrication |

`shadowed_calls` has no PHP shape (D8); the PHP-specific invention to guard against is the
**global-function guess** above, so the corpus carries a `global_fallback` case instead (§5).

### 3.3 Framework edges (P3)

| Framework | Source shape | Fact |
|---|---|---|
| Laravel | `Route::get('/orders', [OrderController::class, 'index'])` in `routes/*.php`; `Route::prefix('/v1')->group(fn () => …)`; string `'Ctl@index'` form | `Endpoint` + `EXPOSES` → `php:NS.Ctl.index`. Literal paths only; a closure handler yields the endpoint and no `EXPOSES` (the Gin rule). `Route::any`/`match` → **nothing** (D2 of [endpoints-typescript-go.md](endpoints-typescript-go.md): no `ANY`). `Route::resource` expands to seven verbs — **defer**, measure demand first. **Fixed after review of #334 (2026-09-08):** only the exact `Route::prefix(lit)->group()` shape had kept its prefix; a chained (`->middleware(...)->group`), reversed, or array-form (`Route::group(['prefix' => …], fn)`) group emitted every route inside at the **wrong path**. Those compose now, and a group whose prefix cannot be read (computed, or a receiver that is not the `Route` facade) emits nothing inside it — a wrong grounded path is the cross-repo false join `go_routes.py` refuses the same way |
| Symfony | `#[Route('/orders', methods: ['GET'])]` on a method, with a class-level prefix | `Endpoint` + `EXPOSES` — the ASP.NET attribute-controller shape from `csharp_extractor.py`; a `#[Route]` with no `methods:` is verb-less → nothing (same D2). **Fixed after review of #334:** the documented named form `#[Route(path: '/api')]` composes; a class-level prefix that cannot be read (`#[Route(self::PREFIX)]`) silences the class rather than dropping the prefix |
| Slim / Lumen | `$app->get('/x', [Ctl::class, 'm'])` | Same reader as Laravel's array form; cheap once it exists |

Module: `php_routes.py`, invoked from `PhpExtractor.extract` the way `go_routes.py` is.

### 3.4 Data layer (P4)

| Framework | Source shape | Fact |
|---|---|---|
| Eloquent | `class Order extends Model`; `$table = 'orders'`; `belongsTo(Customer::class)` / `hasMany(...)` | `Entity` `php:entity:NS.Order` (parallel id, C# precedent) + `REFERENCES` entity→entity. **As built:** the marker is `extends Model` (bare-name match) alone — `$table` is read by nothing; the `Entity`'s `.name` is the class name, and `data_layer_link`'s own normalization (folds case, underscores, a trailing plural `s`) is what pairs `Order`/`orders` against a real schema, so a custom `$table` override was not needed to hit the exit criterion. `belongsTo`/`hasMany`/`hasOne`/`belongsToMany` are read; `morphTo`/`-Many`/`hasOneThrough`/`hasManyThrough` are not — same "measure demand first" discipline as `Route::resource` (§9). **Fixed after review of #334:** `belongsTo(self::class, …)` (the tree relation) had invented `php:entity:NS.self`; a `self`/`static`/`parent` target now emits nothing — `data_layer_link` drops a self-edge anyway |
| Doctrine | `#[ORM\Entity]`, `#[ORM\ManyToOne(targetEntity: X::class)]` | same. **As built:** `OneToMany`/`ManyToMany`/`OneToOne` read too, all matched by bare attribute name |
| Laravel migrations | `Schema::create('orders', fn (Blueprint $t) => …)` — **PHP, not SQL** | Out of scope: the SQL track's authoritative schema comes from `.sql`; a PHP-DSL schema source is its own spec. `data_layer_link` still reconciles Eloquent entities against any `.sql` present, unchanged |

---

## 4. Phases

| Phase | Work | Effort | Exit criteria | Status |
|---|---|---|---|---|
| **P1 Comprehension** | `php_extractor.py` (`PhpExtractor`, `.php`): §3.1 in full, D1/D2/D3/D4/D7; `finalize` repoint of unresolved bases (lift from C#); `_php_parser()` lazy factory. Registry: `default_extractors` gated append, `FRONT_ENDS`, `EXTRA_PROBES`, `_LANG_BY_SUFFIX`, `composer.json` marker + `laravel`/`symfony` framework + `phpunit`/`pest` runner, `scope.NOT_APPLICABLE` (D8), the `php` extension in `docs.py`/`doc_link.py`, D5 `vendor` (after the pinned-corpus check). Packaging (§7). Tests (§6). | ~3–5 d | `pkg extract` on a PHP repo yields Module/Type/Function/Field + IMPORTS/CONTAINS/IMPLEMENTS from 0; `state` renders structure; `pkg verify` 0 dangling after `link_imports`; `pkg capabilities` derives the PHP column; gate green with `--extra php` | ✅ **DONE** |
| **P2 Corpus + CALLS** | `corpus/php/{plain,instance_calls,global_fallback,traits,legacy_require}` labelled **from source, before running** (corpus rule); CALLS per §3.2 rows 1–6; `--scoreboard` regenerated; `test_verifier.py` gets its per-front-end freshness case. | ~3–4 d | Corpus precision **1.00** on every kind (the strict gate); CALLS recall stated with `known_gaps` predicted before the first run; invention status `NOT_APPLICABLE` with reason; `state` reports "Call graph: available" on the validation repo | ✅ **DONE** — precision 1.00 on every node/edge kind (4/4 nodes, 4/4 edges checked); CALLS recall **0.50** (4/8), both misses exactly the predicted `known_gaps` (P3's typed-receiver rule ×3, the global-namespace fallback ×1); `state` confirmed reporting "Call graph: available" |
| **P3 Framework + typed receivers** | `php_routes.py` (Laravel array/string handlers + prefix groups, Symfony attributes); §3.2 typed-receiver rows; `corpus/php/laravel_routes`. | ~3–5 d | `Endpoint` nodes with `EXPOSES` to real handlers; the multi-repo `http` joiner can reach a PHP provider; typed-receiver CALLS measured and pinned in the corpus | ✅ **DONE** — `Endpoint`/`EXPOSES` auto-registered in `pkg capabilities` via `php_routes.py`'s existing delegate-module detection (no registry change needed); typed-receiver `CALLS` resolves 2 of the 3 `instance_calls` misses (the untyped one stays a permanent, honest gap); corpus precision still 1.00 on every kind |
| **P4 Data layer** | Eloquent + Doctrine `Entity`/`REFERENCES` (§3.4); `corpus/php/eloquent`. | ~2–4 d | Entities linked; `data_layer_link` reconciles against a `.sql` schema in the same repo; zero invented `REFERENCES` on the corpus | ✅ **DONE** — new `php_orm.py` (mirroring `python_orm.py`'s naming, not C#'s inline approach); `Entity`/`REFERENCES` auto-registered in `pkg capabilities` via the same delegate-module detection as `php_routes.py`; `data_layer_link` verified generic (matches any non-`sql:`-prefixed `Entity` by name, no PHP-specific code needed); corpus precision 1.00, zero invented `REFERENCES` |

Each phase is its own PR off `develop` on `feat/php-support`, and its own version bump (P1 is
the release-worthy one: "PHP is the 9th language"). Effort is for one engineer familiar with the
PKG. **Rough total: ~11–18 days**, the Go envelope, with no net-new *algorithm* — the design
budget goes to the three rules in §2, not to a method-set match.

**Deviation from plan:** P1, P2, P3, and P4 were built and are landing together rather than as
separate PRs/version bumps — the "each phase is its own PR" convention above held everywhere
except this once; nothing in the design changed as a result, only the shipping granularity.

---

## 5. Corpus cases (P2–P4)

Written per `corpus/README.md`: `.repo/` fixture (dot is load-bearing), `expected.json` by hand
from the source, `known_gaps` predicted before scoring. Vocabulary row to add to the README
table: `php` · module `php:App.Svc` · type `php:App.Svc.Cart` · separator `.`.

| Case | Exercises | The finding it is built to catch |
|---|---|---|
| `plain` | namespace, `use`, class/interface/trait/enum, promoted ctor param, `$this->` and `self::` calls, `new` | The control — 1.00/1.00 by design |
| `instance_calls` | `$obj->m()` on a typed parameter and a typed property; an *untyped* one | P2 must skip all three (recall miss, predicted); P3 resolves the typed two and must still skip the untyped one |
| `global_fallback` | `helper()` where `App\Svc\helper` does **not** exist and `\helper` does, plus one that does exist same-file | The guess (§3.2) — an edge to `php:App.Svc.helper` is a **false positive**; the same-file one is a hit |
| `traits` | a trait used by two classes, one overriding a trait method | D3: `IMPLEMENTS` from both; the override is the class's own `Function`, the trait's is the trait's |
| `legacy_require` | no namespace, `require_once` literal, computed `include` | D7: path-keyed module + one `IMPORTS`; the computed one yields nothing |
| `laravel_routes` (P3) ✅ | array handler, string `Ctl@m`, prefix group (incl. nested), closure handler, `Route::any` | closure → endpoint without `EXPOSES`; `any` → nothing. Slim/Lumen and Symfony attribute routes are covered directly in `test_php_routes.py` rather than a separate corpus case |
| `eloquent` (P4) ✅ | three models, `belongsTo`/`hasMany` between two, one relation to a third-party model | `REFERENCES` between the two; the third-party target stays external (as a node too — external nodes don't count in the corpus score, but the edge to one does). Doctrine's `#[ORM\Entity]`/`#[ORM\ManyToOne]` form is covered directly in `test_php_orm.py` rather than a separate corpus case |

---

## 6. Files to change

**New**

| File | Phase |
|---|---|
| `src/orchestrator/pkg/php_extractor.py` | P1 |
| `tests/pkg/test_php_extractor.py` (module-level `pytest.importorskip("tree_sitter_php")`) | P1 |
| `corpus/php/*/{expected.json,.repo/…}` (§5) | P2–P4 |
| `src/orchestrator/pkg/php_routes.py` + `tests/pkg/test_php_routes.py` | P3 ✅ |
| `src/orchestrator/pkg/php_orm.py` + `tests/pkg/test_php_orm.py` (not originally named — the roadmap's own §6 predates this file; added to mirror `python_orm.py`'s existing precedent rather than inlining entities in `php_extractor.py` like C# does) | P4 ✅ |
| `docs/specs/php-support-roadmap.md` (this file) | now |

**Modified — P1**

| File | Change |
|---|---|
| `pyproject.toml` | `php = ["tree-sitter>=0.21", "tree-sitter-php>=0.23"]`; add `php` to the `languages` meta-extra; `tree_sitter_php` in the mypy `ignore_missing_imports` list |
| `uv.lock` | relock (`uv lock`; the hooks run `--frozen`) |
| `.github/workflows/ci.yml:56` | `--extra php` — without it CI type-checks against a front-end it never imports and the biconditional test proves nothing |
| `src/orchestrator/pkg/extractor.py` | gated append after Go; docstring; `DEFAULT_IGNORE_DIRS` += `"vendor"` (D5) |
| `src/orchestrator/pkg/capabilities.py` | `FrontEnd("php", "php_extractor.py", "PhpExtractor")` before SQL (registry order) |
| `src/orchestrator/doctor.py` | `EXTRA_PROBES["php"] = "tree_sitter_php"` |
| `src/orchestrator/catalog/profile.py` | `.php` → `php`; read `composer.json`; `laravel`/`symfony` needles; `phpunit`/`pest` runners |
| `src/orchestrator/pkg/scope.py` | `NOT_APPLICABLE["php"]` (D8) |
| `src/orchestrator/pkg/import_link.py` | `_match_php_path` (a new matcher) for D7 `require` targets — dispatched on the target body ending in `.php`. **`"php"` deliberately NOT added to `_DOTTED_PREFIXES`** — see D2's correction; that prefix-walk is for languages with re-exports, which PHP has none of, and adding it produced a real false positive the P2 corpus caught |
| `src/orchestrator/pkg/docs.py`, `doc_link.py` | `"php"` in the source-extension sets |
| `tests/pkg/test_default_extractors.py` | biconditional + end-to-end case |
| `tests/pkg/test_capabilities.py` | a PHP entry in `_FIXTURES` |
| `tests/pkg/test_verifier.py` | the per-front-end freshness case |
| `tests/catalog/test_profile.py` | Laravel + PHPUnit case |
| `docs/specs/STATE-OF-SPINE.md` | `8` → `9` front-ends; source-module / test counts via `scripts/state-numbers.py --check` |
| `docs/specs/SPEC-INDEX.md`, `docs/specs/README.md` | index row; spec count `86` → `87` (the state-numbers gate reads it) |
| `docs/specs/language-expansion-roadmap.md` | resolve open question 1; add PHP to the sequence |
| `README.md`, `FEATURES.md`, `USER_GUIDE.md` (both passages: extras list + "Multi-language" blockquote), `KNOWLEDGE_GRAPH.md`, `CHANGELOG.md`, `corpus/README.md` | language lists + the `[php]` extra + the id-vocabulary row |

**Deliberately untouched in this track:** `sdlc/feature_runner.py` `SUPPORTED_LANGUAGES` and
`_resolve_language`, `layout.py`, `scaffold.py`, `testenv.py`, `testrunner.py`, `codegen.py`
prompts, `catalog/catalog.py` conventions skill (D6). `insights.py` (`is_public` → `None`).

---

## 7. Packaging

- `tree-sitter-php` 0.24.1 on PyPI (2026-09-08): abi3 wheels for every CI platform, `py>=3.10`.
  Its only dependency pin (`tree-sitter~=0.24`) sits behind an optional `core` extra, so it
  coexists with the `tree-sitter` 0.26 already in `uv.lock`. **P1 step 1: confirm the parse in
  the project venv** (`uv run --extra php`), not only in the isolated probe used for D1.
- Base install stays stdlib-only: `find_spec("tree_sitter_php")` before a function-local import.
- `doctor` reports the extra; `pkg accuracy` skips PHP cases by name when it is absent.

## 8. Validation targets (ephemeral, docs-only names)

Two poles, both public, shallow-cloned to a temp dir and deleted after — names live in `docs/`
only, never in `src/` or `tests/` (commit `e54ee4c`):

- **BookStack** (`BookStackApp/BookStack`) — a mid-size Laravel app: namespaced, `routes/web.php`
  + `api.php`, Eloquent models with relations, promoted constructors, traits. Exercises P1–P4.
- **WordPress** (`WordPress/WordPress`) — procedural, no namespaces, `require` everywhere, HTML
  mixed into `.php`. Exercises the path-keyed module fallback, D7, and the ERROR-tolerant parse.
  Expect many modules and few types; that is the honest shape of the code.

**Baseline to record before P1:** `pkg extract` on each yields 0 PHP nodes and the profiler
reports no language — the "undetected → empty graph" gap P1 closes.

**Status:** not yet run against either real repo — P1/P2's exit criteria were verified against the
hand-labelled `corpus/php/*` fixtures (§5) and a couple of small ad hoc repos, which is what the
corpus method is for. BookStack/WordPress remain the larger-scale sanity check before P3.

## 9. Deferred (explicitly out of this track)

- **Codegen** — `composer install` → `vendor/bin/phpunit`; layout/scaffold/`PhpToolEnvironment`/
  `PhpTestRunner`/prompts/`php-conventions`. Own spec after P2 proves demand (D6).
- **WordPress hooks** (`add_action`/`add_filter` → callback) — a real call graph for that
  ecosystem, but no `EdgeKind` names "registered as a hook". Do not invent one; measure first.
- **`Route::resource`** expansion, **Blade** templates, **Laravel migration DSL** as a schema
  source, **`__call`/magic** resolution.
- **Preflight** (`php -l` / `phpstan`) — the pre-existing Python-only gap the Go roadmap
  records; belongs with codegen.

## 10. Risks and gotchas

- **The suffix trap (D4).** Forget the `.blade.php` rule and every view becomes a `Module`.
  Test it explicitly; the dispatcher will not.
- **`vendor/` and the ratchet (D5).** Check the pinned five before adding the ignore; a
  changed anchored-fact count is a rebaseline, not a regression — say which in the PR.
- **Global-function fallback.** The one PHP-shaped invention. The `global_fallback` corpus case
  exists so the guess can never be made silently.
- **Backslash ids (D2).** One `\`→`.` conversion site, or three consumers break quietly.
- **`use` scoping is per-file, not per-namespace block.** Braced multi-namespace files
  (`namespace A { … } namespace B { … }`) reset the `use` table per block — handle, rare.
- **`STATE-OF-SPINE` numbers and the spec count are gated**; run `scripts/state-numbers.py
  --check` before pushing. Adding this spec bumps the count in two places.
- **`understand --check` and untracked docs.** This file is in `docs/specs/` on purpose (the
  CLAUDE.md rule); commit it with P1 rather than leaving it local.
- **Never commit `episteme/`** — the post-merge workflow regenerates it, and P1 will change it.

## 11. Sequence and next steps

```
0. Decide D0–D8 (this document)               — ✅ done, D0 revisited once (see D2/D7 corrections)
1. P1 comprehension  → release "PHP is the 9th language"     — ✅ done
2. P2 corpus + CALLS → corpus 1.00 precision, CALLS recall pinned — ✅ done (recall 0.50, all misses predicted)
3. P3 routes + typed receivers → PHP provider in the multi-repo joiner  — ✅ done
4. P4 Eloquent/Doctrine entities                              — ✅ done
5. (follow-on spec) codegen                                   — not started
```
