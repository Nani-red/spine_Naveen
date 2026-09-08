"""PKG: the PHP front-end maps PHP source onto the universal facts (9th language, P1).

tree-sitter-php is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.php_extractor import PhpExtractor

pytest.importorskip("tree_sitter_php", reason="install the 'php' extra")

ORDER_CONTROLLER = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\Order;
use App\\Contracts\\Loggable as Loggy;

trait Loggable
{
    public function log(string $msg): void {}
}

interface Payable
{
    public function pay(): void;
}

class BaseController
{
}

class OrderController extends BaseController implements Payable, Loggy
{
    use Loggable;

    public const MAX_ITEMS = 100;

    public readonly Order $order;

    public function __construct(
        private readonly Order $order,
        protected int $limit = 10,
    ) {
    }

    public function index(): void
    {
    }
}

enum Status: string
{
    case Active = 'active';
    case Inactive = 'inactive';
}
"""


def _facts(
    tmp_path: Path, src: str = ORDER_CONTROLLER, name: str = "OrderController.php"
) -> tuple[FactBatch, str]:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = PhpExtractor()
    module = ex.module_name(f, tmp_path)
    batch = ex.extract(path=f, module=module, rel=name)
    return ex.finalize(batch) or batch, module


def test_module_name_is_the_namespace(tmp_path: Path) -> None:
    _, module = _facts(tmp_path)
    assert module == "App.Http.Controllers"


def test_no_namespace_falls_back_to_repo_relative_path(tmp_path: Path) -> None:
    src = "<?php\nfunction helper() {}\n"
    (tmp_path / "inc").mkdir()
    _, module = _facts(tmp_path, src=src, name="inc/legacy.php")
    assert module == "inc/legacy.php"


def test_emits_type_function_field_nodes(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    ns = "App.Http.Controllers"
    assert by_id[f"php:{ns}.OrderController"].kind is NodeKind.TYPE
    assert by_id[f"php:{ns}.Payable"].kind is NodeKind.TYPE
    assert by_id[f"php:{ns}.Loggable"].kind is NodeKind.TYPE
    assert by_id[f"php:{ns}.Status"].kind is NodeKind.TYPE
    # methods (incl. ctor) are Functions
    assert by_id[f"php:{ns}.OrderController.__construct"].kind is NodeKind.FUNCTION
    assert by_id[f"php:{ns}.OrderController.index"].kind is NodeKind.FUNCTION
    # property, promoted ctor param, class const, and enum case are Fields, `$` stripped
    assert by_id[f"php:{ns}.OrderController.order"].kind is NodeKind.FIELD
    assert by_id[f"php:{ns}.OrderController.limit"].kind is NodeKind.FIELD
    assert by_id[f"php:{ns}.OrderController.MAX_ITEMS"].kind is NodeKind.FIELD
    assert by_id[f"php:{ns}.Status.Active"].kind is NodeKind.FIELD


def test_imports_and_contains_edges(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    ns = "App.Http.Controllers"
    assert (f"php:{ns}", "php:App.Models.Order", EdgeKind.IMPORTS) in edges
    assert (f"php:{ns}", "php:App.Contracts.Loggable", EdgeKind.IMPORTS) in edges
    assert (
        f"php:{ns}.OrderController",
        f"php:{ns}.OrderController.index",
        EdgeKind.CONTAINS,
    ) in edges


def test_extends_implements_and_trait_use_resolve_same_namespace(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    ns = "App.Http.Controllers"
    assert (f"php:{ns}.OrderController", f"php:{ns}.BaseController") in impls  # extends
    assert (f"php:{ns}.OrderController", f"php:{ns}.Payable") in impls  # implements
    assert (f"php:{ns}.OrderController", f"php:{ns}.Loggable") in impls  # trait use (D3)
    # aliased `use ... as Loggy` resolves to the imported FQN, not a same-namespace guess
    assert (f"php:{ns}.OrderController", "php:App.Contracts.Loggable") in impls


def test_blade_template_yields_no_facts(tmp_path: Path) -> None:
    """D4: `.blade.php` is a template, not PHP source — `Path.suffix` is still `.php`."""
    batch, _ = _facts(tmp_path, src="<div>{{ $order->total }}</div>\n", name="order.blade.php")
    assert batch.nodes == []
    assert batch.edges == []


def test_legacy_require_literal_and_computed(tmp_path: Path) -> None:
    """D7: a literal (or __DIR__-relative) require/include is an IMPORTS edge to a
    path-keyed module; a computed one (a variable, a function call) yields nothing."""
    src = (
        "<?php\n"
        "require_once __DIR__ . '/inc/helpers.php';\n"
        "require 'sibling.php';\n"
        "$computed = 'x';\n"
        "include $computed . '.php';\n"
    )
    (tmp_path / "app").mkdir()
    f = tmp_path / "app" / "bootstrap.php"
    f.write_text(src, encoding="utf-8")
    ex = PhpExtractor()
    module = ex.module_name(f, tmp_path)
    batch = ex.extract(path=f, module=module, rel="app/bootstrap.php")
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("php:app/bootstrap.php", "php:app/inc/helpers.php") in imports
    assert ("php:app/bootstrap.php", "php:app/sibling.php") in imports
    assert len(imports) == 2  # the computed `include` contributes nothing


def test_end_to_end_via_repo_extractor_resolves_require_by_path_suffix(tmp_path: Path) -> None:
    """The whole pipeline (RepoCodeExtractor -> link_imports): a literal require in a
    non-namespaced file resolves to the real module it names, D7's payoff."""
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "helpers.php").write_text("<?php\nfunction fmt() {}\n", encoding="utf-8")
    (tmp_path / "index.php").write_text(
        "<?php\nrequire_once __DIR__ . '/inc/helpers.php';\n", encoding="utf-8"
    )
    batch = RepoCodeExtractor([PhpExtractor()]).extract(tmp_path)
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("php:index.php", "php:inc/helpers.php") in imports
    target = next(n for n in batch.nodes if n.id == "php:inc/helpers.php")
    assert target.grounded  # repointed at the real module, not left external


# --- P2: CALLS (§3.2) --------------------------------------------------------

CALLS_FIXTURE = """\
<?php

namespace App\\Svc;

use App\\External\\Vendor;
use function App\\Helpers\\format_money;

class Base
{
    public function boot(): void {}
}

class Cart extends Base
{
    public function run(): void
    {
        $this->total();
        self::helper();
        static::helper();
        parent::boot();
        new Item();
        Item::make();
        Vendor::stamp();
        helper();
        format_money(1);
        \\strlen('x');
        $computed = 'name';
        $computed();
        $this->untyped->method();
    }

    public function total(): void {}

    private static function helper(): void {}
}

function helper(): void {}

class Item {}
"""


def _calls_facts(tmp_path: Path, src: str = CALLS_FIXTURE, name: str = "Cart.php") -> FactBatch:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = PhpExtractor()
    module = ex.module_name(f, tmp_path)
    batch = ex.extract(path=f, module=module, rel=name)
    return ex.finalize(batch) or batch


def test_calls_this_self_static_and_parent(tmp_path: Path) -> None:
    batch = _calls_facts(tmp_path)
    ns = "App.Svc"
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    run = f"php:{ns}.Cart.run"
    assert (run, f"php:{ns}.Cart.total") in calls  # $this->
    assert (run, f"php:{ns}.Cart.helper") in calls  # self:: and static:: both hit the same sibling
    assert (run, f"php:{ns}.Base.boot") in calls  # parent:: — Base is declared in this file


def test_calls_new_and_static_class_call(tmp_path: Path) -> None:
    batch = _calls_facts(tmp_path)
    ns = "App.Svc"
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    run = f"php:{ns}.Cart.run"
    assert (run, f"php:{ns}.Item") in calls  # new Item() — same-file sibling
    assert (run, f"php:{ns}.Item.make") in calls  # Item::make() — same namespace rule


def test_calls_through_an_explicit_use_import(tmp_path: Path) -> None:
    """`Vendor::stamp()` — Vendor is `use`d from outside this namespace, so the call
    resolves to the imported FQN, not a same-namespace guess (mirrors IMPLEMENTS)."""
    batch = _calls_facts(tmp_path)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("php:App.Svc.Cart.run", "php:App.External.Vendor.stamp") in calls


def test_calls_bare_function_same_file_and_use_function(tmp_path: Path) -> None:
    batch = _calls_facts(tmp_path)
    ns = "App.Svc"
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    run = f"php:{ns}.Cart.run"
    assert (run, f"php:{ns}.helper") in calls  # bare helper() — same-file free function
    assert (run, "php:App.Helpers.format_money") in calls  # use function import
    assert (run, "php:strlen") in calls  # \strlen() — fully qualified, unambiguous


def test_calls_never_guesses_dynamic_or_untyped_receivers(tmp_path: Path) -> None:
    """`$computed()`, `$this->untyped->method()` — never fabricated (§3.2's last rows)."""
    batch = _calls_facts(tmp_path)
    calls_from_run = {
        e.dst for e in batch.edges if e.kind is EdgeKind.CALLS and e.src == "php:App.Svc.Cart.run"
    }
    assert not any("untyped" in dst or "method" in dst for dst in calls_from_run)
    # self:: and static:: both target Cart.helper, collapsing to one distinct dst — nine
    # resolvable targets from eleven call sites (the dynamic and untyped ones excluded).
    assert len(calls_from_run) == 9


def test_calls_global_fallback_is_never_guessed(tmp_path: Path) -> None:
    """The trap (§3.2): a bare `helper()` with no same-file declaration and no `use
    function` import must not be guessed as `\\helper` even when that global exists."""
    src = (
        "<?php\n"
        "function helper() {}\n\n"  # global, unrelated to the namespaced call below
        "namespace App\\Svc;\n\n"
        "class Cart {\n"
        "    public function run(): void {\n"
        "        helper();\n"  # ambiguous: App\Svc\helper (undeclared) or \helper?
        "        local();\n"  # same-file, same-namespace — must resolve
        "    }\n"
        "}\n\n"
        "function local() {}\n"
    )
    batch = _calls_facts(tmp_path, src=src, name="Cart2.php")
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    run = "php:App.Svc.Cart.run"
    assert (run, "php:App.Svc.local") in calls
    assert (run, "php:helper") not in calls
    assert (run, "php:App.Svc.helper") not in calls
    assert len(calls) == 1


def test_calls_trait_method_counts_as_the_classs_own_unless_overridden(tmp_path: Path) -> None:
    """D3's `traits` case: a class using a trait can call the trait's method via
    `$this->`/`self::` without redeclaring it; an override wins over the trait's."""
    src = (
        "<?php\n"
        "namespace App\\Svc;\n\n"
        "trait Loggable {\n"
        "    public function log(): void {}\n"
        "    public function tag(): void {}\n"
        "}\n\n"
        "class Job {\n"
        "    use Loggable;\n\n"
        "    public function run(): void {\n"
        "        $this->log();\n"  # not redeclared — must reach the trait's method
        "        $this->tag();\n"  # redeclared below — must reach Job's own override
        "    }\n\n"
        "    public function tag(): void {}\n"
        "}\n"
    )
    batch = _calls_facts(tmp_path, src=src, name="Job.php")
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    run = "php:App.Svc.Job.run"
    assert (run, "php:App.Svc.Loggable.log") in calls
    assert (run, "php:App.Svc.Job.tag") in calls  # the override, not Loggable.tag
    assert (run, "php:App.Svc.Loggable.tag") not in calls


def test_calls_typed_receiver_property_and_parameter(tmp_path: Path) -> None:
    """P3 rows 7-8: a typed property and a typed parameter both resolve; an untyped
    parameter still doesn't (§3.2's documented skip — the `instance_calls` case)."""
    src = (
        "<?php\n"
        "namespace App\\Svc;\n\n"
        "class Handler {\n"
        "    public function run(): void {}\n"
        "}\n\n"
        "class Dispatch {\n"
        "    private Handler $handler;\n\n"
        "    public function __construct(private readonly Handler $ctorHandler) {}\n\n"
        "    public function viaProperty(): void {\n"
        "        $this->handler->run();\n"
        "    }\n\n"
        "    public function viaPromoted(): void {\n"
        "        $this->ctorHandler->run();\n"
        "    }\n\n"
        "    public function viaParameter(Handler $handler): void {\n"
        "        $handler->run();\n"
        "    }\n\n"
        "    public function viaUntyped($handler): void {\n"
        "        $handler->run();\n"
        "    }\n"
        "}\n"
    )
    batch = _calls_facts(tmp_path, src=src, name="Dispatch.php")
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    ns = "App.Svc"
    assert (f"php:{ns}.Dispatch.viaProperty", f"php:{ns}.Handler.run") in calls
    assert (f"php:{ns}.Dispatch.viaPromoted", f"php:{ns}.Handler.run") in calls
    assert (f"php:{ns}.Dispatch.viaParameter", f"php:{ns}.Handler.run") in calls
    untyped_calls = {dst for src_, dst in calls if src_ == f"php:{ns}.Dispatch.viaUntyped"}
    assert not untyped_calls  # no declared type — never guessed


def test_calls_new_x_with_a_wrong_guess_is_repointed_by_finalize(tmp_path: Path) -> None:
    """A `new Vendor()` with no local declaration and no `use` import is a same-namespace
    guess; unresolved in-repo, `finalize` repoints it to a bare external node — the same
    trade-off as an `IMPLEMENTS` guess, never left dangling."""
    src = (
        "<?php\n"
        "namespace App\\Svc;\n\n"
        "class Cart {\n"
        "    public function run(): void {\n"
        "        new Logger();\n"
        "    }\n"
        "}\n"
    )
    batch = _calls_facts(tmp_path, src=src, name="Cart3.php")
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("php:App.Svc.Cart.run", "php:Logger") in calls
    target = next(n for n in batch.nodes if n.id == "php:Logger")
    assert target.external


def test_calls_never_fabricate_relative_scopes_dynamic_names_or_anonymous_this(tmp_path: Path) -> None:
    """Three fabrication shapes found in review (2026-09-08), each a wrong grounded fact:
    `new self()`/`new static()` read as classes called `self`/`static` (which `finalize` then
    materialised as one phantom `Type` per name); `X::$m()` / `$p->$m()` read `$m` as a
    method name; an anonymous class's `$this->helper()` was attributed to the enclosing
    class. `new parent()` with no verified base yields nothing, like `parent::m()`."""
    src = (
        "<?php\n"
        "namespace App\\Svc;\n\n"
        "use App\\Ext\\Vendor;\n\n"
        "class Handler {\n"
        "    public function helper(): void {}\n"
        "    public function run(Handler $p): void {\n"
        "        $a = new self();\n"
        "        $b = new static();\n"
        "        $c = new parent();\n"
        "        $m = 'helper';\n"
        "        Handler::$m();\n"
        "        Vendor::$m();\n"
        "        $p->$m();\n"
        "        $anon = new class {\n"
        "            public function go(): void { $this->helper(); }\n"
        "            public function helper(): void {}\n"
        "        };\n"
        "    }\n"
        "}\n"
    )
    batch = _calls_facts(tmp_path, src=src, name="Handler.php")
    ids = {n.id for n in batch.nodes}
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    run = "php:App.Svc.Handler.run"
    assert not {i for i in ids if i.rsplit(".", 1)[-1] in ("self", "static", "parent", "$m")}
    assert not any(dst.endswith("$m") for _, dst in calls)
    assert (run, "php:App.Svc.Handler.helper") not in calls  # the anonymous class's own $this
    # `new self()` and `new static()` are calls to the enclosing type; nothing else survives.
    assert calls == {(run, "php:App.Svc.Handler")}
