"""The settings: one registry, one stack of layers, and where each value came from.

A memory's behaviour is decided by numbers, and this is where they are written
down. The stack that merges the layers is the shared `config-ultra-rag-mcp`
library, so what is under test here is this server's half of it: the keys it
declares, the values its packaged default ships, the directory names its own
layers resolve by, and the read policy those settings produce.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from config_ultra_rag_mcp import (
    LAYER_COMMAND_LINE,
    LAYER_DEFAULT,
    LAYER_ENVIRONMENT,
    LAYER_FILE,
    LAYER_PROJECT,
    LAYER_USER,
    SettingsError,
    describe_settings,
    merge_settings,
    project_config_path,
    read_config_document,
    resolve_settings,
    user_config_path,
)

from memory_ultra_rag_mcp.settings import (
    PROJECT_CONFIG_RELATIVE,
    SETTINGS,
    SETTINGS_BY_KEY,
    USER_CONFIG_DIRECTORY,
    EffectiveSettings,
    sources_for,
)

EMPTY = {"environ": {}}


def _resolve(project_root: Path, **kwargs) -> EffectiveSettings:
    """Resolve this server's registry over the shared layer stack."""

    values, provenance = resolve_settings(SETTINGS, sources_for(project_root), **kwargs)
    return EffectiveSettings.from_values(values, provenance)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _overlay(tmp_path: Path, text: str) -> Path:
    return _write(tmp_path / "config.toml", text)


def test_the_packaged_file_declares_every_setting() -> None:
    """A key in the code and a key in the file cannot drift apart."""

    document = read_config_document(
        sources_for(Path(".")).default_file, source="default"
    )

    assert set(merge_settings({}, document, SETTINGS, source="default")) == set(
        SETTINGS_BY_KEY
    )


def test_every_setting_is_one_of_the_three_kinds_of_tuning() -> None:
    assert {s.layer for s in SETTINGS} == {"identity", "runtime", "retrieval"}
    assert all(s.doc and s.kind for s in SETTINGS)


def test_every_setting_names_its_own_environment_variable() -> None:
    """The environment layer reads declared names, not a rule this server guesses."""

    assert all(s.env.startswith("MEMORY_ULTRARAG_") for s in SETTINGS)
    assert len({s.env for s in SETTINGS}) == len(SETTINGS)


def test_the_defaults_are_the_ones_the_code_falls_back_to(tmp_path: Path) -> None:
    settings = _resolve(tmp_path, **EMPTY)

    assert settings.cosine_floor == 0.72
    assert settings.embedding_model == "BAAI/bge-small-en-v1.5"
    assert settings.provenance["retrieval.rrf_k"].startswith(LAYER_DEFAULT)


def test_a_layer_names_only_what_it_changes(tmp_path: Path) -> None:
    path = _overlay(tmp_path, "[retrieval]\nrecency_bonus = 0.5\n")

    settings = _resolve(tmp_path, config_path=path, **EMPTY)

    assert settings.recency_bonus == 0.5
    assert settings.rrf_k == 60, "an unset key is not reset by a sibling"
    assert settings.provenance["retrieval.recency_bonus"].startswith(LAYER_FILE)
    assert settings.provenance["retrieval.rrf_k"].startswith(LAYER_DEFAULT)


def test_the_user_layer_applies_and_the_project_layer_wins(tmp_path: Path) -> None:
    """The layer that applies globally, and the one a project may narrow it with."""

    project = tmp_path / "project"
    project_layer = _write(
        project / PROJECT_CONFIG_RELATIVE, "[retrieval]\npool_depth = 7\n"
    )
    account = _overlay(tmp_path, "[retrieval]\npool_depth = 20\n")

    assert user_config_path(USER_CONFIG_DIRECTORY).name == "config.toml"
    assert project_config_path(project, PROJECT_CONFIG_RELATIVE) == project_layer

    bound = replace(sources_for(project), user_config=account)
    values, provenance = resolve_settings(SETTINGS, bound, **EMPTY)

    # The account's value is in force, and the project's is the one that decides.
    assert values["pool_depth"] == 7
    assert LAYER_PROJECT in provenance["retrieval.pool_depth"]
    # And with only the account's layer, that is what the project reads.
    project_layer.unlink()
    alone, provenance = resolve_settings(
        SETTINGS, replace(sources_for(project), user_config=account), **EMPTY
    )
    assert alone["pool_depth"] == 20
    assert LAYER_USER in provenance["retrieval.pool_depth"]


def test_the_environment_and_the_command_line_are_the_strongest_layers(
    tmp_path: Path,
) -> None:
    path = _overlay(tmp_path, "[retrieval]\nrrf_k = 10\nbm25_weight = 2.0\n")

    settings = _resolve(
        tmp_path,
        config_path=path,
        overrides=["retrieval.rrf_k=30"],
        environ={"MEMORY_ULTRARAG_RETRIEVAL_BM25_WEIGHT": "3.0"},
    )

    assert settings.rrf_k == 30
    assert settings.provenance["retrieval.rrf_k"] == LAYER_COMMAND_LINE
    assert settings.bm25_weight == 3.0
    assert settings.provenance["retrieval.bm25_weight"].startswith(LAYER_ENVIRONMENT)


def test_a_number_written_as_text_is_read_the_same_way_everywhere(
    tmp_path: Path,
) -> None:
    """An environment variable and --set carry strings, and a file carries types."""

    from_file = _resolve(
        tmp_path, config_path=_overlay(tmp_path, "[retrieval]\nrrf_k = 30\n"), **EMPTY
    )
    from_env = _resolve(
        tmp_path, environ={"MEMORY_ULTRARAG_RETRIEVAL_RRF_K": "30"}
    )
    from_set = _resolve(tmp_path, overrides=["retrieval.rrf_k=30"], **EMPTY)

    assert from_file.rrf_k == from_env.rrf_k == from_set.rrf_k == 30


def test_an_undeclared_key_is_refused_in_every_layer(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="unknown setting"):
        _resolve(
            tmp_path, config_path=_overlay(tmp_path, "[retrieval]\nnope = 1\n"), **EMPTY
        )
    with pytest.raises(SettingsError, match="unknown setting"):
        _resolve(tmp_path, overrides=["retrieval.nope=1"], **EMPTY)


def test_a_value_out_of_bounds_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="at most 1"):
        _resolve(tmp_path, overrides=["retrieval.recency_bonus=9"], **EMPTY)
    with pytest.raises(SettingsError, match="at least 1"):
        _resolve(tmp_path, overrides=["retrieval.rrf_k=0"], **EMPTY)
    with pytest.raises(SettingsError, match="cosine"):
        _resolve(tmp_path, overrides=["retrieval.cosine_floor=4"], **EMPTY)


def test_a_model_setting_accepts_an_empty_string_because_it_means_something(
    tmp_path: Path,
) -> None:
    """Turning the cross-encoder off is a value, not a missing one."""

    settings = _resolve(tmp_path, overrides=["dense.reranker_model="], **EMPTY)

    assert settings.reranker_model == ""


def test_a_set_without_a_value_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="key=value"):
        _resolve(tmp_path, overrides=["retrieval.rrf_k"], **EMPTY)


def test_a_layer_that_is_not_toml_is_refused_with_its_path(tmp_path: Path) -> None:
    path = _write(tmp_path / "broken.toml", "this is not = = toml\n")

    with pytest.raises(SettingsError, match="not valid TOML"):
        _resolve(tmp_path, config_path=path, **EMPTY)


def test_a_named_file_that_is_missing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="not a readable file"):
        _resolve(tmp_path, config_path=tmp_path / "nowhere.toml", **EMPTY)


def test_a_table_for_a_single_setting_is_refused(tmp_path: Path) -> None:
    path = _overlay(tmp_path, "[retrieval]\nrrf_k = { a = 1 }\n")

    with pytest.raises(SettingsError, match="single setting"):
        _resolve(tmp_path, config_path=path, **EMPTY)


def test_printing_names_every_value_and_the_layer_that_supplied_it(
    tmp_path: Path,
) -> None:
    path = _overlay(tmp_path, "[retrieval]\nrecency_bonus = 0.25\n")

    settings = _resolve(tmp_path, config_path=path, **EMPTY)
    printed = describe_settings(
        SETTINGS, settings.as_values(), settings.provenance
    )

    for setting in SETTINGS:
        assert setting.key in printed
    assert "0.25" in printed
    assert LAYER_FILE in printed
    for section in ("[runtime]", "[retrieval]", "[dense]"):
        assert section in printed


def test_the_policy_the_settings_produce_is_the_read_policy(tmp_path: Path) -> None:
    """What a read does is read off the settings, not off a second copy of them."""

    from memory_ultra_rag_mcp.retrieval import RetrievalSettings

    settings = _resolve(
        tmp_path,
        overrides=["retrieval.rerank_depth=3", "dense.reranker_model="],
        **EMPTY,
    )
    policy = RetrievalSettings.from_settings(settings)

    assert policy.rerank_depth == 3
    assert policy.reranker_model is None
    assert policy.embedding_model == settings.embedding_model
    # And with no settings at all, the fallback is the same policy the file
    # declares, so a caller that skips the files does not quietly get a
    # different memory.
    assert RetrievalSettings.from_settings(None) == RetrievalSettings(
        embedding_model=SETTINGS_BY_KEY["dense.embedding_model"].coerce(
            "BAAI/bge-small-en-v1.5", source="test"
        )
    )
