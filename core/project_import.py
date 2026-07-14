"""Validation and normalization for portable project JSON imports."""
from __future__ import annotations

import copy
import uuid
from typing import Any, Iterable

from core.bot_types import get_bot_plugin


def _unique_name(name: str, existing_names: Iterable[str]) -> str:
    taken = {item.casefold() for item in existing_names}
    if name.casefold() not in taken:
        return name
    candidate = f"{name} (imported)"
    index = 2
    while candidate.casefold() in taken:
        candidate = f"{name} (imported {index})"
        index += 1
    return candidate


def prepare_imported_project(payload: Any, existing_names: Iterable[str]) -> dict[str, Any]:
    """Validate exported project JSON and return a safe, new project object.

    An import always receives a fresh id so it cannot overwrite an existing
    project. The registered plugin provides any defaults absent from an older
    export, then the imported config overlays those defaults.
    """
    if not isinstance(payload, dict):
        raise ValueError("Project file must contain a JSON object.")
    bot_type = payload.get("type")
    if not isinstance(bot_type, str) or not bot_type:
        raise ValueError("Project file is missing a bot type.")
    plugin = get_bot_plugin(bot_type)
    if plugin is None:
        raise ValueError(f"Unsupported bot type: {bot_type}")
    config = payload.get("config", {})
    if not isinstance(config, dict):
        raise ValueError("Project config must be a JSON object.")

    raw_name = payload.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    name = name or plugin.default_project_name
    project = plugin.make_project(
        str(uuid.uuid4())[:8],
        _unique_name(name, existing_names),
    )
    project["config"].update(copy.deepcopy(config))
    return project
