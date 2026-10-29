"""The README's "Repo layout" tree must name every tracked top-level module.

Audit findings (ferraroroberto/photo-ocr#267) landed twice because the
hand-maintained ASCII tree silently fell behind real `src/`, `scripts/` and
vendored-component additions -- nothing forced the two back in sync, and
the heading-based docs ratchet has no notion of "every file is mentioned".
This test walks the tracked filesystem and fails by name when a top-level
`src/`, `scripts/` module or `_vendored/` component is missing from the
tree, catching the next drift at gate time instead of the next audit.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


def _layout_tree_text() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("## Repo layout")
    # The tree lives in the first fenced code block after the heading.
    fence_start = text.index("```", start)
    fence_end = text.index("```", fence_start + 3)
    return text[fence_start:fence_end]


def test_every_top_level_src_module_is_named_in_the_tree() -> None:
    tree = _layout_tree_text()
    modules = sorted(p.name for p in (REPO_ROOT / "src").glob("*.py") if p.name != "__init__.py")
    missing = [m for m in modules if m not in tree]
    assert not missing, f"README.md Repo layout tree omits src/ modules: {missing}"


def test_every_top_level_script_is_named_in_the_tree() -> None:
    tree = _layout_tree_text()
    scripts = sorted(
        p.name
        for p in (REPO_ROOT / "scripts").iterdir()
        if p.is_file() and p.name != "__init__.py"
    )
    missing = [s for s in scripts if s not in tree]
    assert not missing, f"README.md Repo layout tree omits scripts/ entries: {missing}"


def test_every_vendored_component_dir_is_named_in_the_tree() -> None:
    tree = _layout_tree_text()
    vendored_root = REPO_ROOT / "app" / "webapp" / "static" / "_vendored"
    components = sorted(p.name for p in vendored_root.iterdir() if p.is_dir())
    missing = [c for c in components if c not in tree]
    assert not missing, f"README.md Repo layout tree omits _vendored/ components: {missing}"
