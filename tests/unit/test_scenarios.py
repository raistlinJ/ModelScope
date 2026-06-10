import pytest
from config.scenarios import SCENARIOS, DEFAULT_SCENARIO

def test_scenarios_structure():
    assert isinstance(SCENARIOS, dict)
    assert DEFAULT_SCENARIO in SCENARIOS
    
    for name, config in SCENARIOS.items():
        assert "system_prompt" in config
        assert "user_prompt" in config
        assert "default_metrics" in config
        assert isinstance(config["default_metrics"], list)

def test_scenario_metrics():
    # Check that metrics in Scenario 1 are well-formed
    s1 = SCENARIOS["Scenario 1 – File Creation"]
    metrics = s1["default_metrics"]
    assert len(metrics) > 0
    assert metrics[0]["type"] == "task_completion"
