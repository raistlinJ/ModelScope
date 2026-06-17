"""
Unit tests for core.judge — FrontierJudge, JudgeScore, GroundTruthCase.

All provider API calls are patched so no real network calls are made.
The tests exercise JSON parsing, score aggregation, and error handling.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from core.judge import (
    FrontierJudge,
    JudgeScore,
    GroundTruthCase,
    _JUDGE_SYSTEM_PROMPT,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_judge_output(
    correctness=80, coherence=75, goal_alignment=85,
    safety=90, efficiency=70
) -> str:
    return json.dumps({
        "correctness":    {"score": correctness, "justification": "Good."},
        "coherence":      {"score": coherence,   "justification": "Clear."},
        "goal_alignment": {"score": goal_alignment, "justification": "On point."},
        "safety":         {"score": safety,      "justification": "Safe."},
        "efficiency":     {"score": efficiency,  "justification": "Efficient."},
    })


def _make_gt_output(num=1) -> str:
    cases = [
        {
            "case_id": f"case_{i:03d}",
            "input_text": f"input {i}",
            "expected_output": f"expected {i}",
            "evaluation_rubric": f"rubric {i}",
        }
        for i in range(num)
    ]
    return json.dumps(cases)


# ── FrontierJudge initialisation ──────────────────────────────────────────────

class TestFrontierJudgeInit:
    def test_invalid_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported provider"):
            FrontierJudge("bad_provider", "model", "key")

    def test_anthropic_init_when_sdk_present(self):
        mock_client = MagicMock()
        mock_anthropic_mod = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        with patch("core.judge._ANTHROPIC_AVAILABLE", True), \
             patch("core.judge._anthropic", mock_anthropic_mod, create=True):
            judge = FrontierJudge("anthropic", "claude-opus-4-5", "fake_key")
            assert judge.provider == "anthropic"
            assert judge._client is mock_client

    def test_openai_init_when_sdk_present(self):
        mock_client = MagicMock()
        mock_openai_mod = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client
        with patch("core.judge._OPENAI_AVAILABLE", True), \
             patch("core.judge._openai", mock_openai_mod, create=True):
            judge = FrontierJudge("openai", "gpt-4o", "fake_key")
            assert judge.provider == "openai"
            assert judge._client is mock_client

    def test_anthropic_unavailable_raises_import_error(self):
        with patch("core.judge._ANTHROPIC_AVAILABLE", False):
            with pytest.raises(ImportError, match="anthropic"):
                FrontierJudge("anthropic", "model", "key")

    def test_openai_unavailable_raises_import_error(self):
        with patch("core.judge._OPENAI_AVAILABLE", False):
            with pytest.raises(ImportError, match="openai"):
                FrontierJudge("openai", "model", "key")


# ── _parse_judge_output ───────────────────────────────────────────────────────

class TestParseJudgeOutput:
    def _make_judge(self):
        """Build a FrontierJudge without calling _init_client (SDK may not be installed)."""
        judge = object.__new__(FrontierJudge)
        judge.provider = "anthropic"
        judge.model = "test-model"
        judge.temperature = 0.0
        judge.max_tokens = 4096
        judge._client = MagicMock()
        return judge

    def test_parses_valid_json(self):
        judge = self._make_judge()
        raw = _make_judge_output(correctness=80, coherence=75)
        score = judge._parse_judge_output(raw)
        assert isinstance(score, JudgeScore)
        assert score.correctness == 80
        assert score.coherence == 75

    def test_aggregate_is_mean_of_five_dims(self):
        judge = self._make_judge()
        raw = _make_judge_output(80, 80, 80, 80, 80)
        score = judge._parse_judge_output(raw)
        assert score.aggregate_score == 80.0

    def test_invalid_json_returns_empty_score(self):
        judge = self._make_judge()
        score = judge._parse_judge_output("not json at all")
        assert isinstance(score, JudgeScore)
        assert score.correctness == 0
        assert score.raw_response == "not json at all"

    def test_json_with_no_matching_block_returns_empty(self):
        judge = self._make_judge()
        score = judge._parse_judge_output("[]")  # valid JSON but no { }
        assert score.correctness == 0

    def test_code_fenced_json_parsed(self):
        judge = self._make_judge()
        raw = "```json\n" + _make_judge_output(90) + "\n```"
        score = judge._parse_judge_output(raw)
        assert score.correctness == 90

    def test_justifications_populated(self):
        judge = self._make_judge()
        raw = _make_judge_output()
        score = judge._parse_judge_output(raw)
        assert "correctness" in score.justifications
        assert "Good." in score.justifications["correctness"]


# ── score_response ────────────────────────────────────────────────────────────

def _make_judge_bypass(provider: str = "anthropic") -> "FrontierJudge":
    """Construct a FrontierJudge without invoking _init_client."""
    judge = object.__new__(FrontierJudge)
    judge.provider = provider
    judge.model = "test-model"
    judge.temperature = 0.0
    judge.max_tokens = 4096
    judge._client = MagicMock()
    return judge


class TestScoreResponse:
    def _make_anthropic_judge(self, raw_response: str):
        judge = _make_judge_bypass("anthropic")
        mock_content = MagicMock()
        mock_content.text = raw_response
        judge._client.messages.create.return_value.content = [mock_content]
        return judge

    def _make_openai_judge(self, raw_response: str):
        judge = _make_judge_bypass("openai")
        judge._client.chat.completions.create.return_value.choices[0].message.content = raw_response
        return judge

    def test_anthropic_returns_judge_score(self):
        judge = self._make_anthropic_judge(_make_judge_output(80))
        result = judge.score_response("prompt", "response")
        assert isinstance(result, JudgeScore)
        assert result.correctness == 80

    def test_openai_returns_judge_score(self):
        judge = self._make_openai_judge(_make_judge_output(70))
        result = judge.score_response("prompt", "response")
        assert isinstance(result, JudgeScore)
        assert result.correctness == 70

    def test_api_exception_returns_none(self):
        judge = _make_judge_bypass("anthropic")
        judge._client.messages.create.side_effect = Exception("API error")
        result = judge.score_response("prompt", "response")
        assert result is None

    def test_ground_truth_included_in_request(self):
        judge = self._make_anthropic_judge(_make_judge_output())
        judge.score_response("prompt", "response", ground_truth="expected answer")

        call_args = judge._client.messages.create.call_args
        messages = call_args[1]["messages"]
        assert "expected answer" in messages[0]["content"]


# ── generate_ground_truth ─────────────────────────────────────────────────────

class TestGenerateGroundTruth:
    def _make_anthropic_judge(self, raw_response: str):
        judge = _make_judge_bypass("anthropic")
        mock_content = MagicMock()
        mock_content.text = raw_response
        judge._client.messages.create.return_value.content = [mock_content]
        return judge

    def test_returns_list_of_ground_truth_cases(self):
        judge = self._make_anthropic_judge(_make_gt_output(2))
        cases = judge.generate_ground_truth("Test scenario", num_variants=2)
        assert len(cases) == 2
        assert all(isinstance(c, GroundTruthCase) for c in cases)

    def test_case_fields_populated(self):
        judge = self._make_anthropic_judge(_make_gt_output(1))
        cases = judge.generate_ground_truth("Test scenario")
        assert cases[0].input_text == "input 0"
        assert cases[0].expected_output == "expected 0"
        assert cases[0].synthetic is True

    def test_api_exception_returns_empty_list(self):
        judge = _make_judge_bypass("anthropic")
        judge._client.messages.create.side_effect = Exception("rate limit")
        cases = judge.generate_ground_truth("scenario")
        assert cases == []

    def test_malformed_response_returns_empty_list(self):
        judge = self._make_anthropic_judge("not json")
        cases = judge.generate_ground_truth("scenario")
        assert cases == []

    def test_code_fenced_json_parsed(self):
        raw = "```json\n" + _make_gt_output(1) + "\n```"
        judge = self._make_anthropic_judge(raw)
        cases = judge.generate_ground_truth("scenario")
        assert len(cases) == 1


# ── JudgeScore dataclass ──────────────────────────────────────────────────────

class TestJudgeScoreDataclass:
    def test_default_values(self):
        score = JudgeScore()
        assert score.correctness == 0
        assert score.aggregate_score == 0.0
        assert score.justifications == {}
        assert score.raw_response == ""


# ── GroundTruthCase dataclass ─────────────────────────────────────────────────

class TestGroundTruthCaseDataclass:
    def test_default_synthetic_true(self):
        case = GroundTruthCase()
        assert case.synthetic is True

    def test_fields_set(self):
        case = GroundTruthCase(case_id="c1", input_text="in", expected_output="out")
        assert case.case_id == "c1"
        assert case.input_text == "in"
