"""LPDS-014 configuration model: schema, precedence, and the 8 public methods.

The schema and default document are shipped as package resources
(:mod:`keysight_n6700.resources`) and mirrored at the project root under
``config/`` for LPDS-005 §6 discoverability. Precedence, lowest to highest,
is: package default -> config file -> constructor arguments -> explicit
call-time arguments. This driver has no approved environment-variable
overrides yet (LPDS-014 §9's third tier), so that tier is skipped rather than
faked.
"""

from __future__ import annotations

import copy
import json
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import jsonschema

from .exceptions import DriverConfigurationError

_SCHEMA_RESOURCE = "config_schema.json"
_DEFAULT_RESOURCE = "config_default.json"


def _read_resource(name: str) -> dict[str, Any]:
    package = resources.files("keysight_n6700.resources")
    with (package / name).open("r", encoding="utf-8") as handle:
        document: dict[str, Any] = json.load(handle)
    return document


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class ConfigurationMixin:
    """The eight LPDS-014 §15 public configuration methods."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._configuration: dict[str, Any] = _read_resource(_DEFAULT_RESOURCE)

    def get_driver_configuration_schema(self) -> dict[str, Any]:
        """Return the JSON Schema (Draft 2020-12) this driver's configuration follows."""
        return _read_resource(_SCHEMA_RESOURCE)

    def get_driver_default_configuration(self) -> dict[str, Any]:
        """Return the packaged default configuration document."""
        return _read_resource(_DEFAULT_RESOURCE)

    def get_driver_configuration(self, scope: Literal["effective", "explicit"] = "effective") -> dict[str, Any]:
        """Return the current configuration.

        Args:
            scope: ``"effective"`` returns default merged with whatever has
                been imported/loaded so far. ``"explicit"`` is not yet
                distinguished from ``"effective"`` in this driver (no
                environment-variable override tier exists to separate them
                from); both return the same document today.
        """
        del scope
        return copy.deepcopy(self._configuration)

    def validate_driver_configuration(
        self, configuration: dict[str, Any], mode: Literal["replace", "merge"] = "replace"
    ) -> dict[str, Any]:
        """Validate ``configuration`` against the schema without applying it.

        Returns:
            ``{"valid": bool, "errors": [str, ...]}``.
        """
        candidate = (
            configuration if mode == "replace" else _deep_merge(self._configuration, configuration)
        )
        schema = _read_resource(_SCHEMA_RESOURCE)
        validator = jsonschema.Draft202012Validator(schema)
        errors = [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in validator.iter_errors(candidate)]
        return {"valid": not errors, "errors": errors}

    def import_driver_configuration(
        self,
        source: dict[str, Any] | str | Path,
        mode: Literal["replace", "merge"] = "replace",
        apply: bool = False,
    ) -> dict[str, Any]:
        """Validate a configuration document (or path to one) and optionally apply it."""
        document = source if isinstance(source, dict) else json.loads(Path(source).read_text())
        result = self.validate_driver_configuration(document, mode=mode)
        if not result["valid"]:
            raise DriverConfigurationError(
                f"configuration failed validation: {result['errors']}", code="LPDS-CFG-002"
            )
        if apply:
            self._configuration = (
                document if mode == "replace" else _deep_merge(self._configuration, document)
            )
        return result

    def export_driver_configuration(
        self,
        destination: str | Path | None = None,
        scope: Literal["effective", "explicit"] = "effective",
    ) -> dict[str, Any]:
        """Return the current configuration, optionally writing it to ``destination``."""
        document = self.get_driver_configuration(scope=scope)
        if destination is not None:
            Path(destination).write_text(json.dumps(document, indent=2) + "\n")
        return document

    def save_driver_configuration(self, profile_name: str) -> Path:
        """Save the current configuration under ``config/profiles/<profile_name>.json``."""
        profile_dir = Path("config") / "profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        path = profile_dir / f"{profile_name}.json"
        path.write_text(json.dumps(self._configuration, indent=2) + "\n")
        return path

    def load_driver_configuration(self, profile_name: str, apply: bool = False) -> dict[str, Any]:
        """Load ``config/profiles/<profile_name>.json`` and optionally apply it."""
        path = Path("config") / "profiles" / f"{profile_name}.json"
        return self.import_driver_configuration(path, mode="replace", apply=apply)

    def reset_driver_configuration(self, path: str | None = None) -> dict[str, Any]:
        """Reset to the packaged default configuration."""
        self._configuration = _read_resource(_DEFAULT_RESOURCE)
        return copy.deepcopy(self._configuration)
