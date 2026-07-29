from pathlib import Path

from blacknode.packages import load_package


def test_agent_layer_catalog_exposes_memory_and_executive_only():
    info = load_package(Path(__file__).resolve().parents[1])
    assert info.ok
    assert info.layer == "agent"
    assert info.component_mode is True
    assert info.enabled_components == ["memory"]
    assert set(info.components) == {"memory", "executive"}
    assert info.components["executive"]["aliases"] == ["planner"]
    assert info.components["executive"]["deprecated_aliases"]["planner"] == {
        "replacement": "executive",
        "removal_version": "1.0.0",
    }
