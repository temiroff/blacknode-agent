from pathlib import Path

from blacknode.packages import load_package


def test_agent_layer_catalog_loads_with_components_disabled():
    info = load_package(Path(__file__).resolve().parents[1])
    assert info.ok
    assert info.layer == "agent"
    assert info.component_mode is True
    assert info.enabled_components == []
    assert set(info.components) == {"planner", "skill-registry", "mission-review", "confirmation", "memory"}
