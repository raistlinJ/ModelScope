"""
Unit tests for core.judge — LLMJudge, JudgeScore, GroundTruthCase.

All HTTP calls are patched so no real network calls are made.  The tests
exercise config plumbing, JSON parsing, score aggregation, and error handling.
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from core.judge import (
    LLMJudge,
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


def _openai_judge(**kwargs) -> LLMJudge:
    return LLMJudge("OpenAI-Compatible", "judge-model",
                    openai_url="http://judge.local:8080", **kwargs)


def _ollama_judge(**kwargs) -> LLMJudge:
    return LLMJudge("Ollama", "judge-model",
                    ollama_url="http://localhost:11434", **kwargs)


def _openai_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": text}}]}
    return resp


def _ollama_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"message": {"content": text}}
    return resp


# ── LLMJudge initialisation / from_config ─────────────────────────────────────

class TestLLMJudgeInit:
    def test_invalid_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported backend"):
            LLMJudge("anthropic", "model", openai_url="http://x")

    def test_openai_backend_requires_url(self):
        with pytest.raises(ValueError, match="No URL"):
            LLMJudge("OpenAI-Compatible", "model")

    def test_ollama_backend_requires_url(self):
        with pytest.raises(ValueError, match="No Ollama URL"):
            LLMJudge("Ollama", "model")

    def test_from_config_builds_openai_judge(self):
        judge = LLMJudge.from_config({
            "llm_helper_backend": "OpenAI-Compatible",
            "llm_helper_model": "judge-model",
            "llm_helper_openai_url": "https://judge.local:8443/",
            "llm_helper_openai_apikey": "sk-helper",
            "llm_helper_openai_verify_ssl": False,
        })
        assert judge.backend == "OpenAI-Compatible"
        assert judge.model == "judge-model"
        assert judge.openai_url == "https://judge.local:8443"
        assert judge.api_key == "sk-helper"
        assert judge.verify_ssl is False

    def test_from_config_builds_ollama_judge_with_default_url(self):
        judge = LLMJudge.from_config({
            "llm_helper_backend": "Ollama",
            "llm_helper_model": "llama3",
        })
        assert judge.backend == "Ollama"
        assert judge.ollama_url == "http://localhost:11434"


# ── _parse_judge_output ───────────────────────────────────────────────────────

class TestParseJudgeOutput:
    def test_parses_valid_json(self):
        judge = _openai_judge()
        raw = _make_judge_output(correctness=80, coherence=75)
        score = judge._parse_judge_output(raw)
        assert isinstance(score, JudgeScore)
        assert score.correctness == 80
        assert score.coherence == 75

    def test_aggregate_is_mean_of_five_dims(self):
        judge = _openai_judge()
        raw = _make_judge_output(80, 80, 80, 80, 80)
        score = judge._parse_judge_output(raw)
        assert score.aggregate_score == 80.0

    def test_invalid_json_returns_empty_score(self):
        judge = _openai_judge()
        score = judge._parse_judge_output("not json at all")
        assert isinstance(score, JudgeScore)
        assert score.correctness == 0
        assert score.raw_response == "not json at all"

    def test_json_with_no_matching_block_returns_empty(self):
        judge = _openai_judge()
        score = judge._parse_judge_output("[]")  # valid JSON but no { }
        assert score.correctness == 0

    def test_code_fenced_json_parsed(self):
        judge = _openai_judge()
        raw = "```json\n" + _make_judge_output(90) + "\n```"
        score = judge._parse_judge_output(raw)
        assert score.correctness == 90

    def test_justifications_populated(self):
        judge = _openai_judge()
        raw = _make_judge_output()
        score = judge._parse_judge_output(raw)
        assert "correctness" in score.justifications
        assert "Good." in score.justifications["correctness"]


# ── score_response ────────────────────────────────────────────────────────────

class TestScoreResponse:
    @patch("core.judge.requests.post")
    def test_openai_compatible_returns_judge_score(self, mock_post):
        mock_post.return_value = _openai_response(_make_judge_output(80))
        result = _openai_judge().score_response("prompt", "response")
        assert isinstance(result, JudgeScore)
        assert result.correctness == 80
        url = mock_post.call_args[0][0]
        assert url == "http://judge.local:8080/v1/chat/completions"

    @patch("core.judge.requests.post")
    def test_ollama_returns_judge_score(self, mock_post):
        mock_post.return_value = _ollama_response(_make_judge_output(70))
        result = _ollama_judge().score_response("prompt", "response")
        assert isinstance(result, JudgeScore)
        assert result.correctness == 70
        url = mock_post.call_args[0][0]
        assert url == "http://localhost:11434/api/chat"

    @patch("core.judge.requests.post")
    def test_api_key_sent_as_bearer_header(self, mock_post):
        mock_post.return_value = _openai_response(_make_judge_output())
        _openai_judge(api_key="sk-helper").score_response("p", "r")
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-helper"

    @patch("core.judge.requests.post")
    def test_no_auth_header_without_api_key(self, mock_post):
        mock_post.return_value = _openai_response(_make_judge_output())
        _openai_judge().score_response("p", "r")
        headers = mock_post.call_args[1]["headers"]
        assert "Authorization" not in headers

    @patch("core.judge.requests.post")
    def test_api_exception_returns_none(self, mock_post):
        mock_post.side_effect = Exception("API error")
        result = _openai_judge().score_response("prompt", "response")
        assert result is None

    @patch("core.judge.requests.post")
    def test_ground_truth_included_in_request(self, mock_post):
        mock_post.return_value = _openai_response(_make_judge_output())
        _openai_judge().score_response("prompt", "response", ground_truth="expected answer")
        messages = mock_post.call_args[1]["json"]["messages"]
        assert "expected answer" in messages[-1]["content"]

    @patch("core.judge.requests.post")
    def test_system_prompt_sent(self, mock_post):
        mock_post.return_value = _openai_response(_make_judge_output())
        _openai_judge().score_response("prompt", "response")
        messages = mock_post.call_args[1]["json"]["messages"]
        assert messages[0]["role"] == "system"
        assert "expert evaluator" in messages[0]["content"]


# ── generate_ground_truth ─────────────────────────────────────────────────────

class TestGenerateGroundTruth:
    @patch("core.judge.requests.post")
    def test_returns_list_of_ground_truth_cases(self, mock_post):
        mock_post.return_value = _openai_response(_make_gt_output(2))
        cases = _openai_judge().generate_ground_truth("Test scenario", num_variants=2)
        assert len(cases) == 2
        assert all(isinstance(c, GroundTruthCase) for c in cases)

    @patch("core.judge.requests.post")
    def test_case_fields_populated(self, mock_post):
        mock_post.return_value = _openai_response(_make_gt_output(1))
        cases = _openai_judge().generate_ground_truth("Test scenario")
        assert cases[0].input_text == "input 0"
        assert cases[0].expected_output == "expected 0"
        assert cases[0].synthetic is True

    @patch("core.judge.requests.post")
    def test_api_exception_returns_empty_list(self, mock_post):
        mock_post.side_effect = Exception("rate limit")
        cases = _openai_judge().generate_ground_truth("scenario")
        assert cases == []

    @patch("core.judge.requests.post")
    def test_malformed_response_returns_empty_list(self, mock_post):
        mock_post.return_value = _openai_response("not json")
        cases = _openai_judge().generate_ground_truth("scenario")
        assert cases == []

    @patch("core.judge.requests.post")
    def test_code_fenced_json_parsed(self, mock_post):
        mock_post.return_value = _openai_response("```json\n" + _make_gt_output(1) + "\n```")
        cases = _openai_judge().generate_ground_truth("scenario")
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
