from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_diagnosis_frontend_has_no_hardcoded_answers_levels_or_deadline():
    provider = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")
    feature_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "frontend/features/onboarding").glob("*.tsx"))
    )

    assert 'deadline: "2026-08-15"' not in provider
    assert "python_level: 4" not in provider
    assert "is_correct: true" not in provider
    assert "is_correct: false" not in provider
    assert "correct_option_id" not in feature_sources


def test_diagnosis_uses_dynamic_server_questions_and_stable_request_ids_across_retries():
    form = (ROOT / "frontend/features/onboarding/diagnosis-form.tsx").read_text(
        encoding="utf-8"
    )
    api = (ROOT / "frontend/features/onboarding/onboarding-api.ts").read_text(
        encoding="utf-8"
    )

    assert "createDynamicDiagnosticDraft" in form
    assert "draftRequestIdRef.current" in form
    assert "initializeRequestIdRef.current" in form
    assert "locale," in form
    assert 'postRequest<DynamicDiagnosticDraftResponse>("/api/onboarding/dynamic-drafts", request)' in api
    assert 'postRequest<OnboardingInitializationResponse>("/api/onboarding/initialize-from-draft", request)' in api
    assert "diagnostic-template" not in api
    assert '"/api/onboarding/initialize", request' not in api


def test_diagnosis_success_updates_provider_before_navigation():
    provider = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")

    goal_update = provider.index("setGoalId(initialized.goal.goal_id)")
    state_update = provider.index("setState(initialized.state)")
    navigation = provider.index('router.push("/path")', state_update)
    assert goal_update < navigation
    assert state_update < navigation
    assert 'runBusy("path"' in provider
    assert "busyActionsRef.current" in provider


def test_diagnosis_form_does_not_persist_browser_drafts():
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "frontend/features/onboarding").glob("*.*"))
    )

    assert "localStorage" not in sources
    assert "sessionStorage" not in sources
    assert "indexedDB" not in sources
