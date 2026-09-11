"""PKG: the Perl front-end maps Perl source onto the universal facts (10th language;
P1 comprehension, P2 CALLS, P3 routes + typed receivers — perl-support-roadmap.md §3.1-3.3).

tree-sitter-perl is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.perl_extractor import PerlExtractor

pytest.importorskip("tree_sitter_perl", reason="install the 'perl' extra")

CART_PM = """\
use strict;
use warnings;
use Carp qw(croak);
use Shop::Tax;

package Shop::Cart;
use parent -norequire, 'Shop::Base';
with 'Shop::Role::Loggable';

has items => (is => 'rw');
has [qw(a b)];
__PACKAGE__->mk_accessors(qw(c d));

sub new {
    my ($class) = @_;
    return bless {}, $class;
}

sub total {
    my $self = shift;
    return 0;
}

package Shop::Cart::Sub {
    sub nested_sub {
        return 1;
    }
}
"""


def _facts(tmp_path: Path, src: str = CART_PM, name: str = "Cart.pm") -> tuple[FactBatch, str]:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src, encoding="utf-8")
    ex = PerlExtractor()
    module = ex.module_name(f, tmp_path)
    batch = ex.extract(path=f, module=module, rel=name)
    return batch, module


def test_module_name_is_always_the_repo_relative_path(tmp_path: Path) -> None:
    # D2: Module is path-keyed always, unlike a namespace-keyed language.
    _, module = _facts(tmp_path)
    assert module == "Cart.pm"


def test_every_package_is_its_own_type(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.Cart"].kind is NodeKind.TYPE
    assert by_id["perl:Shop.Cart.Sub"].kind is NodeKind.TYPE
    module_node = by_id["perl:Cart.pm"]
    assert module_node.kind is NodeKind.MODULE
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("perl:Cart.pm", "perl:Shop.Cart") in contains
    assert ("perl:Cart.pm", "perl:Shop.Cart.Sub") in contains


def test_subs_are_functions_owned_by_the_package_in_scope(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.Cart.new"].kind is NodeKind.FUNCTION
    assert by_id["perl:Shop.Cart.total"].kind is NodeKind.FUNCTION
    # block-form package: the nested sub belongs to the nested package, not Shop.Cart.
    assert by_id["perl:Shop.Cart.Sub.nested_sub"].kind is NodeKind.FUNCTION
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("perl:Shop.Cart", "perl:Shop.Cart.new") in contains
    assert ("perl:Shop.Cart.Sub", "perl:Shop.Cart.Sub.nested_sub") in contains


def test_implicit_main_keys_subs_on_the_file(tmp_path: Path) -> None:
    src = "sub usage {\n    return 1;\n}\n"
    batch, module = _facts(tmp_path, src=src, name="bin/report.pl")
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:bin/report.pl.usage" in by_id
    assert by_id["perl:bin/report.pl.usage"].kind is NodeKind.FUNCTION
    # No synthetic "Type main" — this is a script with no package statement at all.
    assert not any(n.kind is NodeKind.TYPE for n in batch.nodes)


def test_fields_from_has_mk_accessors(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    for name in ("items", "a", "b", "c", "d"):
        node = by_id[f"perl:Shop.Cart.{name}"]
        assert node.kind is NodeKind.FIELD


def test_field_5_38_class_syntax(tmp_path: Path) -> None:
    src = (
        "use v5.38;\n"
        "use experimental 'class';\n"
        "class Shop::Modern :isa(Shop::Base) {\n"
        "    field $x :param;\n"
        "    field $y :param = 0;\n"
        "    method greet {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    )
    batch, _ = _facts(tmp_path, src=src, name="Modern.pm")
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.Modern"].kind is NodeKind.TYPE
    assert by_id["perl:Shop.Modern.x"].kind is NodeKind.FIELD
    assert by_id["perl:Shop.Modern.y"].kind is NodeKind.FIELD
    assert by_id["perl:Shop.Modern.greet"].kind is NodeKind.FUNCTION
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.Modern", "perl:Shop.Base") in implements


def test_pragmas_are_not_imports(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    imports = {e.dst for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert "perl:strict" not in imports
    assert "perl:warnings" not in imports


def test_generic_use_is_imports_to_a_type_placeholder(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("perl:Cart.pm", "perl:Carp") in imports
    assert ("perl:Cart.pm", "perl:Shop.Tax") in imports
    by_id = {n.id: n for n in batch.nodes}
    # D2: a `use` target names a PACKAGE, so its placeholder is a Type, not a Module —
    # matching the id shape a real `package Shop::Tax` declaration would produce.
    assert by_id["perl:Shop.Tax"].kind is NodeKind.TYPE


def test_use_parent_is_implements_not_imports(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    imports = {e.dst for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert "perl:Shop.Base" not in imports
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.Cart", "perl:Shop.Base") in implements


def test_with_role_is_implements(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.Cart", "perl:Shop.Role.Loggable") in implements


def test_isa_spellings_literal_only(tmp_path: Path) -> None:
    src = (
        "package Shop::A;\n"
        "our @ISA = ('Shop::Base1');\n"
        "package Shop::B;\n"
        "push @ISA, 'Shop::Base2';\n"
        "package Shop::C;\n"
        "our @ISA = (compute_base());\n"  # computed — must yield nothing
        "package Shop::D;\n"
        "use Mojo::Base 'Shop::Base4';\n"
    )
    batch, _ = _facts(tmp_path, src=src, name="Isa.pm")
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.A", "perl:Shop.Base1") in implements
    assert ("perl:Shop.B", "perl:Shop.Base2") in implements
    assert ("perl:Shop.D", "perl:Shop.Base4") in implements
    assert not any(src_id == "perl:Shop.C" for src_id, _ in implements)


def test_require_literal_path_and_bareword(tmp_path: Path) -> None:
    src = 'require "lib/common.pl";\nrequire Shop::Common;\n'
    batch, _ = _facts(tmp_path, src=src, name="bin/report.pl")
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("perl:bin/report.pl", "perl:lib/common.pl") in imports
    assert ("perl:bin/report.pl", "perl:Shop.Common") in imports
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:lib/common.pl"].kind is NodeKind.MODULE
    assert by_id["perl:Shop.Common"].kind is NodeKind.TYPE


def test_computed_require_yields_nothing(tmp_path: Path) -> None:
    src = 'my $x = "common"; require "lib/$x.pl";\n'
    batch, _ = _facts(tmp_path, src=src, name="bin/report.pl")
    assert not any(e.kind is EdgeKind.IMPORTS for e in batch.edges)


def test_end_to_end_via_repo_extractor_resolves_require_by_path_suffix(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "common.pl").write_text("sub helper { 1 }\n", encoding="utf-8")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "report.pl").write_text('require "lib/common.pl";\n', encoding="utf-8")

    batch = RepoCodeExtractor([PerlExtractor()]).extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    target = by_id["perl:lib/common.pl"]
    assert target.grounded is True


# --- P2: CALLS (§3.2) -------------------------------------------------------


def _repo_facts(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, src in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return RepoCodeExtractor([PerlExtractor()]).extract(tmp_path)


def test_calls_sibling_method_via_self_class_package_shift(tmp_path: Path) -> None:
    src = """\
package Shop::Cart;

sub helper { return 1; }

sub a { my $self = shift; $self->helper(); }
sub b { my ($class) = @_; $class->helper(); }
sub c { __PACKAGE__->helper(); }
sub d { shift->helper(); }
sub e { $self->nonexistent(); }
"""
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    for caller in "abcd":
        assert ("perl:Shop.Cart." + caller, "perl:Shop.Cart.helper") in calls
    # `e` calls a name Shop::Cart never declares — never fabricated.
    assert not any(src == "perl:Shop.Cart.e" for src, _ in calls)


def test_calls_has_field_via_self(tmp_path: Path) -> None:
    src = """\
package Shop::Cart;
has items => (is => 'rw');

sub total { my $self = shift; $self->items(); }
"""
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Cart.items") in calls


def test_calls_super(tmp_path: Path) -> None:
    files = {
        "Base.pm": "package Shop::Base;\nsub helper { return 1; }\n",
        "Cart.pm": (
            "package Shop::Cart;\nuse parent -norequire, 'Shop::Base';\n"
            "sub total { my $self = shift; $self->SUPER::helper(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Base.helper") in calls


def test_calls_super_skipped_when_no_base_resolved(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub total { my $self = shift; $self->SUPER::helper(); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_qualified_receiver_new(tmp_path: Path) -> None:
    files = {
        "Log.pm": "package Shop::Log;\nsub new { my ($c) = @_; return bless {}, $c; }\n",
        "Cart.pm": "package Shop::Cart;\nsub total { Shop::Log->new; }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Log.new") in calls


def test_calls_qualified_receiver_falls_back_to_type_when_undeclared(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub total { Shop::External->new; }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.External") in calls
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.External"].external is True


def test_calls_qualified_function(tmp_path: Path) -> None:
    files = {
        "Util.pm": "package Shop::Util;\nsub fmt { return 1; }\n",
        "Cart.pm": "package Shop::Cart;\nsub total { Shop::Util::fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Util.fmt") in calls


def test_calls_bare_same_file_sub(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub helper { return 1; }\nsub total { helper(); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Cart.helper") in calls


def test_calls_bare_explicit_use_qw_import(tmp_path: Path) -> None:
    files = {
        "Util.pm": "package Shop::Util;\nsub fmt { return 1; }\n",
        "Cart.pm": "package Shop::Cart;\nuse Shop::Util qw(fmt);\nsub total { fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Util.fmt") in calls


def test_calls_bare_d10_default_export(tmp_path: Path) -> None:
    files = {
        "Util.pm": "package Shop::Util;\nour @EXPORT = qw(fmt);\nsub fmt { return 1; }\n",
        "Cart.pm": "package Shop::Cart;\nuse Shop::Util;\nsub total { fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Util.fmt") in calls


def test_calls_bare_d10_skips_third_party_default_export(tmp_path: Path) -> None:
    """`use Carp;` (bare) then `croak()` — Carp is never declared in-repo, so D10 must
    refuse, exactly the exporter_default corpus case's control."""
    src = "package Shop::Cart;\nuse Carp;\nsub total { croak('bad'); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_bare_d10_isa_inherited(tmp_path: Path) -> None:
    files = {
        "Base.pm": "package Shop::Base;\nsub helper { return 1; }\n",
        "Cart.pm": ("package Shop::Cart;\nuse parent -norequire, 'Shop::Base';\nsub total { helper(); }\n"),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Base.helper") in calls


def test_calls_bare_unresolved_is_skipped(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub total { nonexistent_sub(); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_never_fabricates_dynamic_or_string_eval_shapes(tmp_path: Path) -> None:
    """Row 6: `&f`, `$self->$m()`, `$obj->can('m')->()`, `goto &f`, string `eval`, and
    `AUTOLOAD` must never produce a CALLS edge — excluded by CST shape, not a blocklist."""
    src = """\
package Shop::Cart;

sub helper { return 1; }

sub risky {
    my $self = shift;
    my $m = 'helper';
    &helper;
    $self->$m();
    eval "helper()";
}
"""
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_instance_calls_typed_receiver_boundary(tmp_path: Path) -> None:
    """The instance_calls corpus control (§3.2 row 7, P3): a literal-constructed receiver
    resolves; an untyped parameter receiver — permanently — does not."""
    files = {
        "Log.pm": (
            "package Shop::Log;\nsub new { my ($c) = @_; return bless {}, $c; }\nsub write { return 1; }\n"
        ),
        "Cart.pm": (
            "package Shop::Cart;\n"
            "sub a { my $log = Shop::Log->new; $log->write; }\n"
            "sub b { my ($self, $thing) = @_; $thing->write; }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.a", "perl:Shop.Log.write") in calls
    assert not any(src == "perl:Shop.Cart.b" for src, _ in calls)


# --- P3: routes (§3.3) ------------------------------------------------------


def test_mojo_full_app_route_string_shorthand(tmp_path: Path) -> None:
    src = (
        "package MyApp;\nuse Mojo::Base 'Mojolicious';\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->get('/orders')->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:endpoint:GET /orders"].kind is NodeKind.ENDPOINT
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /orders", "perl:MyApp.Controller.Orders.index") in exposes


def test_mojo_full_app_route_hash_form(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->get('/orders')->to(controller => 'orders', action => 'index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /orders", "perl:MyApp.Controller.Orders.index") in exposes


def test_mojo_full_app_closure_handler_yields_endpoint_no_exposes(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->get('/orders')->to(sub { return 1; });\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:GET /orders" in by_id
    assert not any(e.kind is EdgeKind.EXPOSES for e in batch.edges)


def test_mojo_full_app_any_verb_yields_nothing(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->any('/orders')->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    assert not any(n.kind is NodeKind.ENDPOINT for n in batch.nodes)


def test_mojo_full_app_computed_path_yields_nothing(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    my $id = 1;\n    $r->get(\"/orders/$id\")->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    assert not any(n.kind is NodeKind.ENDPOINT for n in batch.nodes)


def test_mojo_under_group_composes_prefix(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    my $api = $r->under('/api');\n"
        "    $api->get('/orders')->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:GET /api/orders" in by_id


def test_mojo_lite_closure_route(tmp_path: Path) -> None:
    src = "get '/x' => sub {\n    return 1;\n};\n"
    batch = _repo_facts(tmp_path, {"app.pl": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:GET /x" in by_id
    assert not any(e.kind is EdgeKind.EXPOSES for e in batch.edges)


def test_mojo_lite_named_handler_route(tmp_path: Path) -> None:
    src = "get '/y' => \\&handler;\n\nsub handler {\n    return 1;\n}\n"
    batch = _repo_facts(tmp_path, {"app.pl": src})
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /y", "perl:app.pl.handler") in exposes
