"""Every tunable this server reads, as one registry and one stack of layers.

The settings are declared in a table here and shipped as ``default.toml`` beside
this module, so a value that exists in code and a value a file may set cannot
drift apart: a layer may only set a key this registry declares, and a key the
registry does not declare is an error rather than a silent default.

The layers, lowest precedence first:

1. the packaged ``default.toml``, which holds every setting;
2. the user's ``config.toml``, in the account's config directory — the one that
   applies globally, since a memory is shared by every project on the account;
3. the project's own ``config.toml``, inside the project's own state, for a
   project that wants to differ from the account;
4. a file named with ``--config``;
5. ``MEMORY_ULTRARAG_*`` environment variables;
6. ``--set key=value`` on the command line.

Each layer names only the keys it changes, and every effective value reports the
layer that supplied it, so ``--print-config`` can say where a number came from
rather than only what it is.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import platformdirs

__all__ = [
    "DEFAULT_CONFIG_FILENAME",
    "PROJECT_CONFIG_RELATIVE",
    "SETTINGS",
    "SETTINGS_BY_KEY",
    "SETTINGS_ENVIRONMENT_PREFIX",
    "EffectiveSettings",
    "Setting",
    "SettingsError",
    "default_config_path",
    "describe_settings",
    "merge_settings",
    "project_config_path",
    "read_config_document",
    "resolve_settings",
    "user_config_path",
]

#: The layer names, lowest first. `provenance` reports these strings, so
#: `--print-config` says where each effective value came from.
LAYER_DEFAULT = "default"
LAYER_USER = "user config"
LAYER_PROJECT = "project config"
LAYER_FILE = "--config"
LAYER_ENVIRONMENT = "environment"
LAYER_COMMAND_LINE = "command line"

DEFAULT_CONFIG_FILENAME = "default.toml"
USER_CONFIG_DIRECTORY = "memory-ultra-rag-mcp"
PROJECT_CONFIG_RELATIVE = Path(".memory-rag") / "config.toml"
SETTINGS_ENVIRONMENT_PREFIX = "MEMORY_ULTRARAG_"

#: What a setting decides, which is the contract that keeps configurability honest.
#:
#: * ``identity`` — the value decides what a memory is made of. It is written beside
#:   the statements it produced, so changing it means the statements are embedded
#:   again rather than compared across two spaces.
#: * ``runtime`` — the value chooses where something lives or how the process is
#:   shaped. Changing it moves or resizes something without changing what a
#:   statement is.
#: * ``retrieval`` — the value decides what a read admits and in what order.
Layer = Literal["identity", "runtime", "retrieval"]


class SettingsError(ValueError):
    """Raised when a settings layer is unreadable, unknown, or out of bounds."""


@dataclass(frozen=True, slots=True)
class Setting:
    """One tunable: where it lives, what it accepts, and how it is named.

    ``layer`` is the contract that keeps configurability honest:

    * ``identity`` — the value decides what a memory is made of. It is recorded
      with the statements it produced, so changing it means they are embedded
      again rather than compared across two spaces.
    * ``runtime`` — the value chooses where something lives, and changing it moves
      or resizes something without changing what a statement is.
    * ``retrieval`` — the value decides what a read admits and in what order.
    """

    key: str
    field: str
    kind: type
    layer: Layer
    doc: str
    minimum: float | None = None
    maximum: float | None = None
    # A setting that accepts an empty string uses it to mean something of its own —
    # this package's own cache, no cross-encoder — rather than a missing value.
    empty: str | None = None

    def coerce(self, raw: Any, *, source: str) -> Any:
        """Return ``raw`` as this setting's type, or refuse it by name.

        The same call serves every layer, so a number written in TOML and the
        same number written in the environment or after ``--set`` are read one
        way and fail the same way.
        """

        where = f"{self.key} ({source})"
        if isinstance(raw, bool) and self.kind is not bool:
            raise SettingsError(f"{where} must be {self.kind.__name__}, not a boolean")
        if isinstance(raw, str) and not raw.strip() and self.empty is not None:
            return self.empty
        if isinstance(raw, str) and not raw.strip() and self.kind is str:
            return ""
        try:
            value = self.kind(raw)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{where} must be {self.kind.__name__}") from exc
        if self.kind is str:
            value = value.strip()
        if self.minimum is not None and value < self.minimum:
            raise SettingsError(f"{where} must be at least {self.minimum:g}")
        if self.maximum is not None and value > self.maximum:
            raise SettingsError(f"{where} must be at most {self.maximum:g}")
        return value


SETTINGS: tuple[Setting, ...] = (
    Setting(
        "runtime.model_cache_root",
        "model_cache_root",
        str,
        "runtime",
        "Shared FastEmbed model cache.",
        empty="",
    ),
    Setting(
        "retrieval.cosine_floor",
        "cosine_floor",
        float,
        "retrieval",
        "The cosine a dense candidate must clear to be admitted at all.",
        minimum=-1.0,
        maximum=1.0,
    ),
    Setting(
        "retrieval.relative_margin",
        "relative_margin",
        float,
        "retrieval",
        "How far under its own best a candidate may be and still be admitted.",
        minimum=0.0,
        maximum=1.0,
    ),
    Setting(
        "retrieval.bm25_weight",
        "bm25_weight",
        float,
        "retrieval",
        "The weight the words contribute to a fused score.",
        minimum=0.0,
    ),
    Setting(
        "retrieval.dense_weight",
        "dense_weight",
        float,
        "retrieval",
        "The weight the meaning contributes to a fused score.",
        minimum=0.0,
    ),
    Setting(
        "retrieval.rrf_k",
        "rrf_k",
        int,
        "retrieval",
        "The reciprocal rank fusion constant; larger flattens the sides' ranks.",
        minimum=1.0,
    ),
    Setting(
        "retrieval.recency_bonus",
        "recency_bonus",
        float,
        "retrieval",
        "What a statement is worth for being the newest of those that matched.",
        minimum=0.0,
        maximum=1.0,
    ),
    Setting(
        "retrieval.duplicate_cosine",
        "duplicate_cosine",
        float,
        "retrieval",
        "How alike two statements may be before the second is refused.",
        minimum=-1.0,
        maximum=2.0,
    ),
    Setting(
        "retrieval.pool_depth",
        "pool_depth",
        int,
        "retrieval",
        "How many candidates each side contributes before fusion.",
        minimum=1.0,
    ),
    Setting(
        "retrieval.rerank_depth",
        "rerank_depth",
        int,
        "retrieval",
        "How many fused candidates the cross-encoder reads.",
        minimum=0.0,
    ),
    Setting(
        "dense.embedding_model",
        "embedding_model",
        str,
        "identity",
        "The local model a scope's vectors are built with.",
    ),
    Setting(
        "dense.reranker_model",
        "reranker_model",
        str,
        "identity",
        "The local cross-encoder that orders candidates, or empty for none.",
        empty="",
    ),
)

SETTINGS_BY_KEY = {setting.key: setting for setting in SETTINGS}
SETTINGS_BY_FIELD = {setting.field: setting for setting in SETTINGS}
SETTINGS_SECTIONS = tuple(dict.fromkeys(s.key.split(".")[0] for s in SETTINGS))


@dataclass(frozen=True, slots=True)
class EffectiveSettings:
    """The values in force for one process, and where each came from."""

    runtime: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    dense: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_values(
        cls, values: Mapping[str, Any], provenance: Mapping[str, str]
    ) -> EffectiveSettings:
        """Return the settings, grouped the way the file is."""

        grouped: dict[str, dict[str, Any]] = {
            section: {} for section in SETTINGS_SECTIONS
        }
        for setting in SETTINGS:
            grouped[setting.key.split(".")[0]][setting.field] = values[setting.field]
        return cls(provenance=dict(provenance), **grouped)

    def __getattr__(self, name: str) -> Any:
        """Return a setting by field name, so callers read ``settings.rerank_depth``."""

        for section in SETTINGS_SECTIONS:
            found = getattr(self, section, None)
            if isinstance(found, dict) and name in found:
                return found[name]
        raise AttributeError(name)

    def cache_root(self) -> str:
        """Return the configured shared cache directory, or "" for this package's own.

        Named apart from the setting it carries: a field is read straight off an
        ``EffectiveSettings``, so a method of the same name would answer instead of
        the value.
        """

        return str(self.runtime["model_cache_root"])


def default_config_path() -> Path:
    """Return the packaged default config file."""

    return Path(__file__).with_name(DEFAULT_CONFIG_FILENAME)


def user_config_path() -> Path:
    """Return the per-user overlay path for this platform.

    This is the layer that applies globally: one file in the account's config
    directory changes every project on the account.
    """

    return platformdirs.user_config_path(USER_CONFIG_DIRECTORY) / "config.toml"


def project_config_path(project_root: str | Path) -> Path:
    """Return the per-project overlay path, inside the project's own state."""

    return Path(project_root) / PROJECT_CONFIG_RELATIVE


def read_config_document(path: Path, *, source: str) -> dict[str, Any]:
    """Read one TOML layer, refusing anything that is not a plain document."""

    if path.is_symlink():
        raise SettingsError(f"{source} must not be a symlink: {path}")
    if not path.is_file():
        raise SettingsError(f"{source} is not a readable file: {path}")
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{source} is not valid TOML ({path}): {exc}") from exc
    except OSError as exc:
        raise SettingsError(f"{source} cannot be read ({path}): {exc}") from exc
    if not isinstance(document, dict):
        raise SettingsError(f"{source} must contain a TOML table: {path}")
    return document


def merge_settings(
    base: dict[str, Any],
    overlay: Mapping[str, Any],
    *,
    source: str,
    prefix: str = "",
) -> set[str]:
    """Merge one layer into ``base`` per key, and return the keys it set.

    Tables merge recursively, so a layer names only what it changes. A scalar
    replaces whatever the layer below had. A key the registry does not declare is
    an error, which is what makes a typo loud instead of silent.
    """

    written: set[str] = set()
    for key, value in overlay.items():
        if not isinstance(key, str):
            raise SettingsError(f"{source} has a non-string key: {key!r}")
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            if dotted in SETTINGS_BY_KEY:
                raise SettingsError(
                    f"{source} gives a table for the single setting {dotted}"
                )
            written |= merge_settings(base, value, source=source, prefix=f"{dotted}.")
            continue
        if dotted not in SETTINGS_BY_KEY:
            raise SettingsError(f"{source} sets an unknown setting: {dotted}")
        base[dotted] = value
        written.add(dotted)
    return written


def environment_settings(
    environ: Mapping[str, str],
) -> dict[str, tuple[str, str]]:
    """Return the environment layer as ``key -> (raw value, variable name)``."""

    values: dict[str, tuple[str, str]] = {}
    for key in SETTINGS_BY_KEY:
        name = f"{SETTINGS_ENVIRONMENT_PREFIX}{key.replace('.', '_').upper()}"
        raw = environ.get(name)
        if raw is None or not raw.strip():
            # A variable that is unset, or set to nothing, is not a layer saying
            # anything. Turning the cross-encoder off is therefore a file or a
            # `--set` value rather than an empty variable.
            continue
        values[key] = (raw.strip(), name)
    return values


def override_settings(overrides: Sequence[str]) -> dict[str, str]:
    """Return the ``--set`` layer, parsed as ``key=value`` pairs."""

    values: dict[str, str] = {}
    for item in overrides:
        name, separator, value = str(item).partition("=")
        if not separator:
            raise SettingsError(f"--set needs key=value, got {item!r}")
        key = name.strip()
        if key not in SETTINGS_BY_KEY:
            raise SettingsError(
                f"--set names an unknown setting: {key}; the settings are "
                + ", ".join(SETTINGS_BY_KEY.keys())
            )
        values[key] = value.strip()
    return values


def resolve_settings(
    project_root: str | Path,
    *,
    config_path: str | Path | None = None,
    overrides: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
) -> EffectiveSettings:
    """Merge every layer, lowest precedence first, and report where each came from.

    Nothing is read relative to the working directory: a client may start this
    server anywhere, so the project layer is placed by ``project_root`` and the
    user layer by the platform's config directory.
    """

    environment = os.environ if environ is None else environ
    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}

    layers: list[tuple[str, Path]] = [(LAYER_DEFAULT, default_config_path())]
    for name, path in (
        (LAYER_USER, user_config_path()),
        (LAYER_PROJECT, project_config_path(project_root)),
    ):
        if path.exists():
            layers.append((name, path))
    if config_path is not None:
        layers.append((LAYER_FILE, Path(config_path).expanduser()))

    for name, layer_path in layers:
        document = read_config_document(layer_path, source=name)
        written = merge_settings(merged, document, source=f"{name} ({layer_path})")
        for key in written:
            provenance[key] = f"{name} ({layer_path})"

    for key, (raw, variable) in environment_settings(environment).items():
        merged[key] = raw
        provenance[key] = f"{LAYER_ENVIRONMENT} ({variable})"

    for key, raw in override_settings(overrides).items():
        merged[key] = raw
        provenance[key] = LAYER_COMMAND_LINE

    values: dict[str, Any] = {}
    for key, setting in SETTINGS_BY_KEY.items():
        if key not in merged:
            raise SettingsError(
                f"No value for {key}: the packaged default must declare every setting"
            )
        try:
            values[setting.field] = setting.coerce(
                merged[key], source=provenance.get(key, LAYER_DEFAULT)
            )
        except SettingsError as error:
            raise SettingsError(
                f"{key} from {provenance.get(key, LAYER_DEFAULT)}: {error}"
            ) from error
    return EffectiveSettings.from_values(values, provenance)


def describe_settings(settings: EffectiveSettings) -> str:
    """Render the effective settings, one line per key, with the layer that set it."""

    lines = [
        "Effective settings, later layers overriding earlier ones:",
        "  default.toml < user config < project config < --config < environment < --set",
    ]
    width = max(len(setting.key) for setting in SETTINGS)
    for section in SETTINGS_SECTIONS:
        lines.append("")
        lines.append(f"[{section}]")
        for setting in SETTINGS:
            if not setting.key.startswith(f"{section}."):
                continue
            value = getattr(settings, setting.field)
            rendered = '""' if value == "" else repr(value)
            if isinstance(value, str):
                rendered = f'"{value}"'
            lines.append(
                f"  {setting.key:<{width}} = {rendered:<28} "
                f"# {settings.provenance.get(setting.key, LAYER_DEFAULT)}"
            )
    return "\n".join(lines)
