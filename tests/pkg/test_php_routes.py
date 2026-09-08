"""Laravel/Slim and Symfony routes in PHP source → `Endpoint` nodes + `EXPOSES` edges (P3, §3.3).

Before this, a PHP route handler had zero callers — `impact_of` reported a public endpoint
safe to refactor, and no PHP repo could be a provider in a cross-repo `http` join.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.php_extractor import PhpExtractor

pytest.importorskip("tree_sitter_php", reason="install the 'php' extra")


def _graph(tmp_path: Path, src: str, name: str = "routes.php") -> tuple[set[str], set[tuple[str, str]]]:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = PhpExtractor()
    module = ex.module_name(f, tmp_path)
    batch = ex.extract(path=f, module=module, rel=name)
    endpoints = {n.id for n in batch.nodes if n.kind is NodeKind.ENDPOINT}
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    return endpoints, exposes


LARAVEL_ROUTES = """<?php

namespace App\\Http;

use App\\Http\\Controllers\\OrderController;

Route::get('/orders', [OrderController::class, 'index']);
Route::post('/orders', 'OrderController@store');
Route::any('/anything', [OrderController::class, 'index']);
Route::match(['get', 'post'], '/matched', [OrderController::class, 'index']);
Route::get('/closure', function () {
    return 1;
});

Route::prefix('/v1')->group(function () {
    Route::get('/orders', [OrderController::class, 'index']);

    Route::prefix('/admin')->group(function () {
        Route::get('/orders', [OrderController::class, 'index']);
    });
});
"""


def test_array_handler_form(tmp_path: Path) -> None:
    endpoints, exposes = _graph(tmp_path, LARAVEL_ROUTES)
    assert "php:endpoint:GET /orders" in endpoints
    assert ("php:endpoint:GET /orders", "php:App.Http.Controllers.OrderController.index") in exposes


def test_string_handler_form(tmp_path: Path) -> None:
    endpoints, exposes = _graph(tmp_path, LARAVEL_ROUTES)
    assert "php:endpoint:POST /orders" in endpoints
    assert ("php:endpoint:POST /orders", "php:App.Http.Controllers.OrderController.store") in exposes


def test_any_and_match_yield_nothing(tmp_path: Path) -> None:
    """D2 of endpoints-typescript-go.md: no `ANY` — `Route::any`/`match` register nothing."""
    endpoints, _ = _graph(tmp_path, LARAVEL_ROUTES)
    assert not any("/anything" in e or "/matched" in e for e in endpoints)


def test_closure_handler_yields_endpoint_without_exposes(tmp_path: Path) -> None:
    endpoints, exposes = _graph(tmp_path, LARAVEL_ROUTES)
    assert "php:endpoint:GET /closure" in endpoints
    assert not any(src == "php:endpoint:GET /closure" for src, _ in exposes)


def test_prefix_group_composes_and_nests(tmp_path: Path) -> None:
    endpoints, exposes = _graph(tmp_path, LARAVEL_ROUTES)
    assert "php:endpoint:GET /v1/orders" in endpoints
    assert "php:endpoint:GET /v1/admin/orders" in endpoints
    assert ("php:endpoint:GET /v1/orders", "php:App.Http.Controllers.OrderController.index") in exposes
    assert ("php:endpoint:GET /v1/admin/orders", "php:App.Http.Controllers.OrderController.index") in exposes


def test_slim_array_handler_form(tmp_path: Path) -> None:
    src = (
        "<?php\n"
        "namespace App;\n\n"
        "use App\\Controllers\\OrderController;\n\n"
        "$app->get('/orders', [OrderController::class, 'index']);\n"
        "$other->get('/ignored', [OrderController::class, 'index']);\n"
    )
    endpoints, exposes = _graph(tmp_path, src, name="slim.php")
    assert "php:endpoint:GET /orders" in endpoints
    assert ("php:endpoint:GET /orders", "php:App.Controllers.OrderController.index") in exposes
    # a receiver that isn't `$app` is not read as a route registration at all.
    assert "php:endpoint:GET /ignored" not in endpoints


def test_symfony_attribute_route_with_class_prefix(tmp_path: Path) -> None:
    src = (
        "<?php\n"
        "namespace App\\Controller;\n\n"
        "#[Route('/api')]\n"
        "class ApiController\n"
        "{\n"
        "    #[Route('/orders', methods: ['GET'])]\n"
        "    public function index(): void\n"
        "    {\n"
        "    }\n\n"
        "    #[Route('/orders')]\n"
        "    public function verbLess(): void\n"
        "    {\n"
        "    }\n"
        "}\n"
    )
    endpoints, exposes = _graph(tmp_path, src, name="ApiController.php")
    assert "php:endpoint:GET /api/orders" in endpoints
    assert ("php:endpoint:GET /api/orders", "php:App.Controller.ApiController.index") in exposes
    # a verb-less #[Route] responds to everything — nothing asserted (same D2 as Route::any).
    assert not any("verbLess" in dst for _, dst in exposes)
