import pytest
from config.scenarios import (
    SCENARIOS, DEFAULT_SCENARIO, validate_scenarios, REQUIRED_SCENARIO_KEYS,
)

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


# ── Schema validation ─────────────────────────────────────────────────────────

def test_shipped_scenarios_are_valid():
    """The bundled registry must satisfy its own schema (runs at import too)."""
    validate_scenarios()  # no exception


@pytest.mark.parametrize("missing_key", REQUIRED_SCENARIO_KEYS)
def test_missing_required_key_raises(missing_key):
    spec = dict(SCENARIOS[DEFAULT_SCENARIO])
    spec.pop(missing_key)
    with pytest.raises(ValueError, match=missing_key):
        validate_scenarios({"Broken": spec})


def test_partial_caf_config_raises():
    spec = dict(SCENARIOS[DEFAULT_SCENARIO])
    spec["caf_scope"] = "Narrow"  # caf_urgency intentionally absent
    with pytest.raises(ValueError, match="caf_urgency"):
        validate_scenarios({"PartialCAF": spec})


def test_non_dict_scenario_raises():
    with pytest.raises(ValueError, match="must be a dict"):
        validate_scenarios({"Bad": ["not", "a", "dict"]})


def test_fail_patterns_must_be_list():
    spec = dict(SCENARIOS[DEFAULT_SCENARIO])
    spec["fail_patterns"] = "oops-a-string"
    with pytest.raises(ValueError, match="fail_patterns"):
        validate_scenarios({"BadPatterns": spec})
