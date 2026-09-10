"""A nested git checkout is a boundary, not a subdirectory.

A submodule (or a vendored clone, or a worktree) inside a repository has its own HEAD, its
own clean/dirty state and, in a multi-repo graph, its own scope key. Before this rule both
walkers descended into it, so the outer repo presented the inner repo's symbols as its own
— and when both were declared in ``.spine/repos.yaml`` every symbol existed twice under two
ids, with nothing to flag it. Measured on a superproject with one submodule:
``py:lib@lib.core.helper`` and ``py:super@libs.lib.lib.core.helper`` in one merged graph.

No real ``git`` here: the boundary marker is the ``.git`` entry, and a submodule's is a
*file*, so the tests plant both forms by hand and never shell out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.doc_source import read_doc_pages
from orchestrator.pkg.extractor import RepoCodeExtractor, is_nested_repo
from orchestrator.pkg.persistence import load_or_extract_repos
from orchestrator.pkg.repos import RepoConfigError, from_mapping


def _superproject(root: Path, *, git_marker: str = "file") -> Path:
    """``root/`` with one module of its own and a nested checkout at ``libs/lib``.

    ``git_marker`` picks how the inner checkout announces itself: ``"file"`` as a submodule
    does (``.git`` is a pointer file), ``"dir"`` as a plain clone does.
    """
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# super\n\nOuter docs.\n", encoding="utf-8")
    inner = root / "libs" / "lib"
    (inner / "lib").mkdir(parents=True)
    (inner / "lib" / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (inner / "README.md").write_text("# lib\n\nInner docs.\n", encoding="utf-8")
    if git_marker == "file":
        (inner / ".git").write_text("gitdir: ../../.git/modules/libs/lib\n", encoding="utf-8")
    else:
        (inner / ".git").mkdir()
    return inner


@pytest.mark.parametrize("marker", ["file", "dir"])
def test_a_nested_checkout_is_recognised_by_either_git_marker(tmp_path: Path, marker: str) -> None:
    _superproject(tmp_path, git_marker=marker)
    assert is_nested_repo(tmp_path / "libs", "lib")
    assert not is_nested_repo(tmp_path, "app")


@pytest.mark.parametrize("marker", ["file", "dir"])
def test_code_extraction_stops_at_a_nested_checkout(tmp_path: Path, marker: str) -> None:
    _superproject(tmp_path, git_marker=marker)
    batch = RepoCodeExtractor().extract(tmp_path)
    files = {n.provenance.file for n in batch.nodes if n.provenance and n.provenance.file}
    assert any(f.endswith("app/main.py") for f in files)
    assert not any(f.endswith("core.py") for f in files), sorted(files)


def test_a_plain_subdirectory_is_still_walked(tmp_path: Path) -> None:
    """The boundary is the ``.git`` entry — a directory without one is part of this repo."""
    inner = tmp_path / "libs" / "lib" / "lib"
    inner.mkdir(parents=True)
    (inner / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    files = {n.provenance.file for n in batch.nodes if n.provenance and n.provenance.file}
    assert any(f.endswith("core.py") for f in files)


def test_doc_ingestion_stops_at_a_nested_checkout(tmp_path: Path) -> None:
    _superproject(tmp_path)
    titles = {p.source_file for p in read_doc_pages(tmp_path)}
    assert "README.md" in titles
    assert not any(t.startswith("libs/lib/") for t in titles), sorted(titles)


def test_a_declared_root_inside_another_is_accepted_when_it_is_a_checkout(tmp_path: Path) -> None:
    """A submodule declared beside its superproject: the outer walk stops at it, so no overlap."""
    _superproject(tmp_path)
    repo_set = from_mapping({"super": ".", "lib": "libs/lib"}, base=tmp_path)
    assert [k for k, _ in repo_set] == ["lib", "super"]


def test_a_declared_root_inside_another_is_refused_when_it_is_a_plain_directory(tmp_path: Path) -> None:
    """No boundary means both keys scope the same files — refused at load, naming both."""
    (tmp_path / "libs" / "lib").mkdir(parents=True)
    with pytest.raises(RepoConfigError, match="'lib'.*inside repo 'super'.*not a git checkout"):
        from_mapping({"super": ".", "lib": "libs/lib"}, base=tmp_path)


def test_a_superproject_and_its_submodule_merge_without_a_double_count(tmp_path: Path) -> None:
    """The measured failure: the same function under two scope keys. Now exactly one."""
    _superproject(tmp_path)
    repo_set = from_mapping({"super": ".", "lib": "libs/lib"}, base=tmp_path)
    merged = load_or_extract_repos(repo_set, cache_dir=tmp_path / "cache")
    core = sorted(
        n.id
        for n in merged.batch.nodes
        if n.provenance and n.provenance.file and n.provenance.file.endswith("core.py")
    )
    assert core == ["py:lib@lib.core", "py:lib@lib.core.helper"]
