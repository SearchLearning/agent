from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.orchestrator as orchestrator


def test_build_hybrid_weight_profile_note_normalizes_unknown_to_balanced():
    note = orchestrator._build_hybrid_weight_profile_note("unknown")
    assert note == "Hybrid Weight Profile: balanced"


def test_read_hybrid_weight_profile_choice_returns_semantic_for_mostly_semantic(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "read_single_choice_input",
        lambda **kwargs: orchestrator._QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC,
    )
    profile = orchestrator._read_hybrid_weight_profile_choice()
    assert profile == orchestrator._HYBRID_WEIGHT_OPTION_SEMANTIC


def test_read_hybrid_weight_profile_choice_returns_lexical_for_mostly_exact(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "read_single_choice_input",
        lambda **kwargs: orchestrator._QUERY_PATTERN_OPTION_MOSTLY_EXACT,
    )
    profile = orchestrator._read_hybrid_weight_profile_choice()
    assert profile == orchestrator._HYBRID_WEIGHT_OPTION_LEXICAL


def test_read_hybrid_weight_profile_choice_returns_balanced_for_balanced(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "read_single_choice_input",
        lambda **kwargs: orchestrator._QUERY_PATTERN_OPTION_BALANCED,
    )
    profile = orchestrator._read_hybrid_weight_profile_choice()
    assert profile == orchestrator._HYBRID_WEIGHT_OPTION_BALANCED


def test_read_hybrid_weight_profile_choice_normalizes_unknown_to_balanced_default(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "read_single_choice_input",
        lambda **kwargs: "unexpected-value",
    )
    profile = orchestrator._read_hybrid_weight_profile_choice()
    assert profile == orchestrator._HYBRID_WEIGHT_OPTION_BALANCED


def test_read_hybrid_weight_profile_choice_uses_field_context_and_three_options(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_read_single_choice_input(**kwargs):
        captured.update(kwargs)
        return orchestrator._QUERY_PATTERN_OPTION_MOSTLY_EXACT

    monkeypatch.setattr(orchestrator, "read_single_choice_input", _fake_read_single_choice_input)

    profile = orchestrator._read_hybrid_weight_profile_choice(
        [" title ", "", "description", "TITLE"]
    )

    assert profile == orchestrator._HYBRID_WEIGHT_OPTION_LEXICAL
    assert captured["title"] == "Semantic Search Query Pattern"
    assert captured["default_value"] == orchestrator._QUERY_PATTERN_OPTION_BALANCED
    options = captured["options"]
    assert isinstance(options, list)
    assert len(options) == 3
    prompt = str(captured["prompt"])
    assert prompt.startswith(
        "From your sample data, fields like title, description look text-heavy."
    )
    assert "Query pattern: How do you expect users to search?" in prompt
    assert options[0][1] == 'Mostly exact keywords (like "Carmencita 1894")'
    assert options[1][1] == 'Semantic/natural language (like "early silent films about dancers")'
    assert options[2][1] == "Balanced mix of both (default)"


def test_is_semantic_dominant_query_pattern_true_for_semantic():
    assert orchestrator._is_semantic_dominant_query_pattern(
        orchestrator._HYBRID_WEIGHT_OPTION_SEMANTIC
    )


def test_is_semantic_dominant_query_pattern_false_for_lexical_and_balanced():
    assert not orchestrator._is_semantic_dominant_query_pattern(
        orchestrator._HYBRID_WEIGHT_OPTION_LEXICAL
    )
    assert not orchestrator._is_semantic_dominant_query_pattern(
        orchestrator._HYBRID_WEIGHT_OPTION_BALANCED
    )


def test_requires_model_deployment_preference_true_for_balanced_and_semantic():
    assert orchestrator._requires_model_deployment_preference(
        orchestrator._HYBRID_WEIGHT_OPTION_BALANCED
    )
    assert orchestrator._requires_model_deployment_preference(
        orchestrator._HYBRID_WEIGHT_OPTION_SEMANTIC
    )


def test_requires_model_deployment_preference_false_for_lexical():
    assert not orchestrator._requires_model_deployment_preference(
        orchestrator._HYBRID_WEIGHT_OPTION_LEXICAL
    )


def test_build_context_notes_skips_semantic_and_deployment_notes_when_text_not_required():
    state = orchestrator.SessionState(
        inferred_text_search_required=False,
        hybrid_weight_profile=orchestrator._HYBRID_WEIGHT_OPTION_BALANCED,
        model_deployment_preference=orchestrator._MODEL_DEPLOYMENT_OPTION_SAGEMAKER_ENDPOINT,
    )

    notes = orchestrator._build_context_notes(state)

    assert "Requirements note: semantic query-pattern preference =" not in notes
    assert "Hybrid Weight Profile:" not in notes
    assert "Requirements note: production model deployment preference =" not in notes
