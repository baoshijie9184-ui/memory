import pytest

from desaymem_light.bootstrap.registry import PluginRegistry
from desaymem_light.domain.errors import DuplicatePluginError, PluginNotFoundError


def test_registry_builds_selected_plugin_with_isolated_options() -> None:
    registry: PluginRegistry[dict] = PluginRegistry("example")
    registry.register("v1", lambda *, options: {"options": options})

    instance = registry.create("v1", options={"limit": 10})

    assert instance == {"options": {"limit": 10}}


def test_registry_rejects_duplicate_names() -> None:
    registry: PluginRegistry[object] = PluginRegistry("example")
    registry.register("v1", object)
    with pytest.raises(DuplicatePluginError):
        registry.register("v1", object)


def test_registry_reports_unknown_plugin() -> None:
    registry: PluginRegistry[object] = PluginRegistry("example")
    with pytest.raises(PluginNotFoundError, match="available: <none>"):
        registry.create("missing")
