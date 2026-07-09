"""Bot-type plugin registry."""

from core.bot_types.base import BotTypePlugin, ProjectTemplate, StatusItem
from core.bot_types.registry import (
    bot_plugin_discovery_errors,
    discover_bot_plugins,
    get_bot_plugin,
    iter_bot_plugins,
    refresh_bot_plugins,
    require_bot_plugin,
)

__all__ = [
    "BotTypePlugin",
    "ProjectTemplate",
    "StatusItem",
    "bot_plugin_discovery_errors",
    "discover_bot_plugins",
    "get_bot_plugin",
    "iter_bot_plugins",
    "refresh_bot_plugins",
    "require_bot_plugin",
]
