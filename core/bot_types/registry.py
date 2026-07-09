"""Discovery-backed registry for bot-type plugins."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import os
import pkgutil
import sys
from collections import OrderedDict
from importlib import metadata
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType
from typing import Iterable

from core.bot_types.base import BotTypePlugin


ENTRY_POINT_GROUP = "modelscope.bot_types"
PLUGIN_PATH_ENV = "MODELSCOPE_BOT_PLUGIN_PATH"
_SKIP_MODULE_NAMES = {"base", "registry"}

_PLUGINS: OrderedDict[str, BotTypePlugin] | None = None
_DISCOVERY_ERRORS: list[str] = []


def _register_plugin(plugins: OrderedDict[str, BotTypePlugin], plugin: BotTypePlugin) -> None:
    if not plugin.type_id:
        raise ValueError(f"{plugin.__class__.__name__} must define type_id")
    plugins[plugin.type_id] = plugin


def _plugins_from_module(module: ModuleType) -> Iterable[BotTypePlugin]:
    for _, obj in inspect.getmembers(module):
        if inspect.isclass(obj):
            if obj is BotTypePlugin or not issubclass(obj, BotTypePlugin):
                continue
            if obj.__module__ != module.__name__:
                continue
            yield obj()
        elif isinstance(obj, BotTypePlugin):
            yield obj


def _iter_builtin_modules() -> Iterable[ModuleType]:
    import core.bot_types as package

    for module_info in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        short_name = module_info.name.rsplit(".", 1)[-1]
        if short_name.startswith("_") or short_name in _SKIP_MODULE_NAMES:
            continue
        yield importlib.import_module(module_info.name)


def _entry_points() -> Iterable[object]:
    try:
        eps = metadata.entry_points()
        if hasattr(eps, "select"):
            return eps.select(group=ENTRY_POINT_GROUP)
        return eps.get(ENTRY_POINT_GROUP, [])
    except Exception as exc:
        _DISCOVERY_ERRORS.append(f"entry point discovery failed: {exc}")
        return ()


def _load_plugin_file(path: Path) -> ModuleType:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    module_name = f"_modelscope_bot_plugin_{path.stem}_{digest}"
    spec = importlib_util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load plugin file {path}")
    module = importlib_util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _iter_plugin_files() -> Iterable[Path]:
    candidates: list[Path] = [
        Path.cwd() / "plugins" / "bot_types",
        Path.home() / ".modelscope" / "bot_types",
    ]
    env_paths = os.environ.get(PLUGIN_PATH_ENV, "")
    candidates.extend(Path(part).expanduser() for part in env_paths.split(os.pathsep) if part.strip())

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)

        if resolved.is_file() and resolved.suffix == ".py" and not resolved.name.startswith("_"):
            yield resolved
        elif resolved.is_dir():
            for plugin_file in sorted(resolved.glob("*.py")):
                if not plugin_file.name.startswith("_"):
                    yield plugin_file


def _discover() -> OrderedDict[str, BotTypePlugin]:
    plugins: OrderedDict[str, BotTypePlugin] = OrderedDict()

    for module in _iter_builtin_modules():
        for plugin in _plugins_from_module(module):
            _register_plugin(plugins, plugin)

    for entry_point in _entry_points():
        try:
            loaded = entry_point.load()
            if isinstance(loaded, ModuleType):
                for plugin in _plugins_from_module(loaded):
                    _register_plugin(plugins, plugin)
            elif inspect.isclass(loaded) and issubclass(loaded, BotTypePlugin):
                _register_plugin(plugins, loaded())
            elif isinstance(loaded, BotTypePlugin):
                _register_plugin(plugins, loaded)
            else:
                _DISCOVERY_ERRORS.append(f"{entry_point.name} did not load a bot plugin")
        except Exception as exc:
            _DISCOVERY_ERRORS.append(f"{entry_point.name} failed: {exc}")

    for plugin_file in _iter_plugin_files():
        try:
            module = _load_plugin_file(plugin_file)
            for plugin in _plugins_from_module(module):
                _register_plugin(plugins, plugin)
        except Exception as exc:
            _DISCOVERY_ERRORS.append(f"{plugin_file} failed: {exc}")

    return plugins


def discover_bot_plugins(refresh: bool = False) -> OrderedDict[str, BotTypePlugin]:
    global _PLUGINS, _DISCOVERY_ERRORS
    if _PLUGINS is None or refresh:
        _DISCOVERY_ERRORS = []
        _PLUGINS = _discover()
    return _PLUGINS


def refresh_bot_plugins() -> tuple[BotTypePlugin, ...]:
    return tuple(discover_bot_plugins(refresh=True).values())


def bot_plugin_discovery_errors() -> tuple[str, ...]:
    discover_bot_plugins()
    return tuple(_DISCOVERY_ERRORS)


def iter_bot_plugins() -> tuple[BotTypePlugin, ...]:
    return tuple(discover_bot_plugins().values())


def get_bot_plugin(type_id: str | None) -> BotTypePlugin | None:
    if not type_id:
        return None
    return discover_bot_plugins().get(type_id)


def require_bot_plugin(type_id: str) -> BotTypePlugin:
    plugin = get_bot_plugin(type_id)
    if plugin is None:
        raise KeyError(f"Unknown bot type: {type_id}")
    return plugin
