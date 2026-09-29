"""What this package may import, and what it may no longer keep.

Two boundaries are here because they are the two that have been broken. The
store, the index, and the vectors must not reach for a model library, or a model
change stops being a line of `default.toml` and the suite stops running with no
model installed. And the layer machinery must not come back: it is the pinned
`config-ultra-rag-mcp` library's, and a second copy is a second set of bugs.

Imports are read with `ast` rather than executed, so the test cannot be fooled
by an import that only succeeds in one environment.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "memory_ultra_rag_mcp"

#: The modules that answer a read, and the reason they may not import a model.
MODEL_FREE = ("store.py", "index.py", "vectors.py")

#: A retrieval or index model library, in any of the names it is imported under.
MODEL_LIBRARIES = frozenset(
    {"fastembed", "onnxruntime", "numpy", "sentence_transformers", "torch"}
)

#: The layer stack, the registry's `Setting` type, the coercion, the provenance,
#: and the three path helpers live in the pinned `config-ultra-rag-mcp` library.
LAYER_MACHINERY = frozenset(
    {
        "LAYER_DEFAULT",
        "Setting",
        "SettingsError",
        "SettingsSources",
        "default_config_path",
        "describe_settings",
        "environment_settings",
        "merge_settings",
        "override_settings",
        "project_config_path",
        "read_config_document",
        "resolve_settings",
        "user_config_path",
    }
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_roots(path: Path) -> set[str]:
    """Return every absolute top-level module name this file imports."""

    names: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_store_and_the_index_import_no_model_library() -> None:
    """Retrieval is written against the seams in `models.py`, so a model change
    stays a line of `default.toml` and the suite runs with no model at all."""

    offenders = {
        name: sorted(_imported_roots(PACKAGE / name) & MODEL_LIBRARIES)
        for name in MODEL_FREE
        if _imported_roots(PACKAGE / name) & MODEL_LIBRARIES
    }

    assert offenders == {}, f"model imports in store, index, or vectors: {offenders}"


def test_the_settings_module_defines_no_layer_machinery() -> None:
    """A second copy of the stack is a second set of bugs, and it has happened once."""

    defined = {
        node.name
        for node in _parse(PACKAGE / "settings.py").body
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }

    assert not defined & LAYER_MACHINERY, (
        "settings.py defines layer machinery that belongs to the pinned library: "
        f"{sorted(defined & LAYER_MACHINERY)}"
    )


def test_the_settings_module_still_declares_the_registry() -> None:
    """The keys, their bounds, and the packaged default are this server's own."""

    from memory_ultra_rag_mcp.settings import SETTINGS, SETTINGS_BY_KEY, sources_for

    assert len(SETTINGS) == len(SETTINGS_BY_KEY)
    assert all(setting.env for setting in SETTINGS)
    assert sources_for("/tmp").default_file.name == "default.toml"
    assert sources_for("/tmp").default_file.is_file()
