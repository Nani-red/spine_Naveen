"""PKG: the Perl front-end maps Perl source onto the universal facts (10th language, P1).

Comprehension only — no CALLS here, that's P2 (perl-support-roadmap.md §3.2).
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
