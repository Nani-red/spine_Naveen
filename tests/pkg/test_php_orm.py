"""Eloquent + Doctrine ORM models in PHP source → `Entity` nodes + `REFERENCES` edges (P4, §3.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.data_layer_link import link_data_layer
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.php_extractor import PhpExtractor

pytest.importorskip("tree_sitter_php", reason="install the 'php' extra")

ELOQUENT = """<?php

namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Model;

class Customer extends Model
{
}

class Order extends Model
{
    public function customer()
    {
        return $this->belongsTo(Customer::class);
    }

    public function items()
    {
        return $this->hasMany(OrderItem::class);
    }

    public function notification()
    {
        return $this->belongsTo(\\Vendor\\Notifications\\DatabaseNotification::class);
    }
}
"""


def _facts(tmp_path: Path, src: str, name: str = "Models.php") -> FactBatch:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = PhpExtractor()
    module = ex.module_name(f, tmp_path)
    batch = ex.extract(path=f, module=module, rel=name)
    return ex.finalize(batch) or batch


def test_eloquent_models_become_entities(tmp_path: Path) -> None:
    batch = _facts(tmp_path, ELOQUENT)
    entities = {n.id: n for n in batch.nodes if n.kind is NodeKind.ENTITY}
    assert "php:entity:App.Models.Customer" in entities
    assert "php:entity:App.Models.Order" in entities
    assert entities["php:entity:App.Models.Customer"].grounded
    assert entities["php:entity:App.Models.Order"].grounded
    # the Type node stays exactly as it was — Entity is added alongside, not instead of.
    assert any(n.id == "php:App.Models.Order" and n.kind is NodeKind.TYPE for n in batch.nodes)


def test_belongs_to_and_has_many_become_references(tmp_path: Path) -> None:
    batch = _facts(tmp_path, ELOQUENT)
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    assert ("php:entity:App.Models.Order", "php:entity:App.Models.Customer") in refs
    assert ("php:entity:App.Models.Order", "php:entity:App.Models.OrderItem") in refs


def test_third_party_relation_target_stays_external(tmp_path: Path) -> None:
    """A relation to a class this repo never declares still gets a `REFERENCES` edge —
    to an external `Entity` node, not a dangling one (the `eloquent` corpus case's rule)."""
    batch = _facts(tmp_path, ELOQUENT)
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    target = "php:entity:Vendor.Notifications.DatabaseNotification"
    assert ("php:entity:App.Models.Order", target) in refs
    node = next(n for n in batch.nodes if n.id == target)
    assert node.external


DOCTRINE = """<?php

namespace App\\Entity;

use Doctrine\\ORM\\Mapping as ORM;

#[ORM\\Entity]
class Category
{
}

#[ORM\\Entity]
class Product
{
    #[ORM\\ManyToOne(targetEntity: Category::class)]
    private Category $category;

    private string $name;
}
"""


def test_doctrine_attribute_entities_and_relations(tmp_path: Path) -> None:
    batch = _facts(tmp_path, DOCTRINE, name="Entities.php")
    entities = {n.id for n in batch.nodes if n.kind is NodeKind.ENTITY}
    assert "php:entity:App.Entity.Category" in entities
    assert "php:entity:App.Entity.Product" in entities
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    assert ("php:entity:App.Entity.Product", "php:entity:App.Entity.Category") in refs


def test_a_plain_class_is_never_mistaken_for_an_entity(tmp_path: Path) -> None:
    """The highest-risk rule (python_orm.py's own words): no ORM marker, no Entity — never
    the shape of the class."""
    src = "<?php\nnamespace App\\Models;\n\nclass PlainValue\n{\n    private string $x;\n}\n"
    batch = _facts(tmp_path, src, name="Plain.php")
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENTITY]


def test_data_layer_link_reconciles_against_a_sql_schema(tmp_path: Path) -> None:
    """`data_layer_link` needs nothing PHP-specific — it matches any non-`sql:`-prefixed
    Entity by name alone."""
    (tmp_path / "app" / "Models").mkdir(parents=True)
    (tmp_path / "app" / "Models" / "Order.php").write_text(
        "<?php\nnamespace App\\Models;\n\nuse Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class Order extends Model\n{\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "schema.sql").write_text("CREATE TABLE orders (id INT PRIMARY KEY);\n", encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    batch = link_data_layer(batch)
    entities = [n for n in batch.nodes if n.kind is NodeKind.ENTITY]
    sql_entities = [n for n in entities if n.id.startswith("sql:")]
    php_entities = [n for n in entities if n.id == "php:entity:App.Models.Order"]
    assert sql_entities  # the schema table exists
    assert not php_entities  # the ORM entity collapsed onto it, per link_data_layer's contract
