"""LPDS-014 configuration model: schema, precedence, and public methods."""

from __future__ import annotations

import copy
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import jsonschema

from .exceptions import DriverConfigurationError

_SCHEMA_RESOURCE = "config_schema.json"
_DEFAULT_RESOURCE = "config_default.json"
_PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _read_resource(name: str) -> dict[str, Any]:
    package = resources.files("keysight_n6700.resources")
    try:
        with (package / name).open("r", encoding="utf-8") as handle:
            document: dict[str, Any] = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DriverConfigurationError(
            f"cannot read packaged configuration {name!r}: {exc}", code="LPDS-CFG-005"
        ) from exc
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
    """LPDS-014 configuration methods with safe file handling."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._configuration: dict[str, Any] = _read_resource(_DEFAULT_RESOURCE)
        self._profile_dir = Path("config") / "profiles"

    def _profile_path(self, name: str) -> Path:
        if not _PROFILE_RE.fullmatch(name) or name in {".", ".."}:
            raise DriverConfigurationError(
                f"invalid profile name {name!r}", code="LPDS-CFG-004"
            )
        return self._profile_dir / f"{name}.json"

    def _notify_configuration_applied(self) -> None:
        callback = getattr(self, "_apply_configuration", None)
        if callable(callback):
            callback()

    def get_driver_configuration_schema(self) -> dict[str, Any]:
        return _read_resource(_SCHEMA_RESOURCE)

    def get_driver_default_configuration(self) -> dict[str, Any]:
        return _read_resource(_DEFAULT_RESOURCE)

    def get_driver_configuration(
        self, scope: Literal["effective", "explicit"] = "effective"
    ) -> dict[str, Any]:
        del scope
        return copy.deepcopy(self._configuration)

    def validate_driver_configuration(
        self, configuration: dict[str, Any], mode: Literal["replace", "merge"] = "replace"
    ) -> dict[str, Any]:
        if mode not in {"replace", "merge"}:
            raise DriverConfigurationError(f"invalid configuration mode {mode!r}", code="LPDS-CFG-006")
        candidate = (
            copy.deepcopy(configuration)
            if mode == "replace"
            else _deep_merge(self._configuration, configuration)
        )
        schema = _read_resource(_SCHEMA_RESOURCE)
        validator = jsonschema.Draft202012Validator(schema)
        errors = [
            f"{'/'.join(str(part) for part in error.path)}: {error.message}"
            for error in validator.iter_errors(candidate)
        ]
        return {"valid": not errors, "errors": errors}

    def import_driver_configuration(
        self,
        source: dict[str, Any] | str | Path,
        mode: Literal["replace", "merge"] = "replace",
        apply: bool = False,
    ) -> dict[str, Any]:
        try:
            document = (
                copy.deepcopy(source)
                if isinstance(source, dict)
                else json.loads(Path(source).read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise DriverConfigurationError(
                f"cannot read configuration: {exc}", code="LPDS-CFG-005"
            ) from exc
        if not isinstance(document, dict):
            raise DriverConfigurationError("configuration root must be an object", code="LPDS-CFG-002")
        result = self.validate_driver_configuration(document, mode=mode)
        if not result["valid"]:
            raise DriverConfigurationError(
                f"configuration failed validation: {result['errors']}", code="LPDS-CFG-002"
            )
        if apply:
            self._configuration = (
                copy.deepcopy(document)
                if mode == "replace"
                else _deep_merge(self._configuration, document)
            )
            self._notify_configuration_applied()
        return result

    def export_driver_configuration(
        self,
        destination: str | Path | None = None,
        scope: Literal["effective", "explicit"] = "effective",
    ) -> dict[str, Any]:
        document = self.get_driver_configuration(scope=scope)
        if destination is not None:
            try:
                Path(destination).write_text(
                    json.dumps(document, indent=2) + "\n", encoding="utf-8"
                )
            except OSError as exc:
                raise DriverConfigurationError(
                    f"cannot write configuration: {exc}", code="LPDS-CFG-007"
                ) from exc
        return document

    def save_driver_configuration(self, profile_name: str) -> Path:
        path = self._profile_path(profile_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._configuration, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            raise DriverConfigurationError(
                f"cannot save profile {profile_name!r}: {exc}", code="LPDS-CFG-007"
            ) from exc
        return path

    def load_driver_configuration(
        self, profile_name: str, apply: bool = False
    ) -> dict[str, Any]:
        return self.import_driver_configuration(
            self._profile_path(profile_name), mode="replace", apply=apply
        )

    def reset_driver_configuration(self, path: str | None = None) -> dict[str, Any]:
        if path is not None:
            self.import_driver_configuration(path, mode="replace", apply=True)
        else:
            self._configuration = _read_resource(_DEFAULT_RESOURCE)
            self._notify_configuration_applied()
        return copy.deepcopy(self._configuration)
