from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_assessment_submit_does_not_autofill_blank_answers():
    source = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")

    assert "I need to revisit" not in source
    assert "assessmentAnswers[item.item_id]?.trim() ||" not in source
    assert "assessmentAnswers[item.item_id]?.trim() ?? \"\"" in source


def test_assessment_submit_sends_a_uuid_request_id_with_the_answers():
    source = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")

    assert "const assessmentRequestId = crypto.randomUUID();" in source
    assert "{ request_id: assessmentRequestId, answers }" in source


def test_frontend_assessment_types_match_the_public_contract_only():
    source = (ROOT / "frontend/lib/learning-data.ts").read_text(encoding="utf-8")
    assessment_types = source.split("export type AssessmentOption =", 1)[1].split(
        "export type AssessmentResult =", 1
    )[0]

    for public_field in ("option_id", "label", "options", "difficulty", "status", "scope"):
        assert public_field in assessment_types
    for secret_field in ("reference_answer", "rubric_json", "source_chunk_ids", "is_correct"):
        assert secret_field not in assessment_types


def test_demo_mode_is_explicitly_marked_in_frontend_shell():
    provider = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")
    shell = (ROOT / "frontend/components/learning-shell.tsx").read_text(encoding="utf-8")

    assert "isDemoMode: boolean" in provider
    assert 'const isDemoMode = goalBootstrap === "no_goal";' in provider
    assert 'data-testid="demo-mode-banner"' in shell
    assert 't("shell.demoMode")' in shell


def test_official_source_search_uses_bearer_auth_without_client_identity():
    source = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")
    api = (ROOT / "frontend/lib/api.ts").read_text(encoding="utf-8")

    assert re.search(
        r'postRequest<\{ results: SourceResult\[\] \}>\(\s*'
        r'"/api/tools/search-official-learning-sources",\s*'
        r'\{.*?domains: \["fastapi\.tiangolo\.com", "docs\.python\.org", "platform\.openai\.com"\].*?\}\s*\)',
        source,
        re.DOTALL,
    )
    assert 'headers.set("Authorization", `Bearer ${accessToken}`)' in api
    assert "X-User-Id" not in source


def test_onboarding_initialization_is_sent_as_one_atomic_request():
    source = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")
    onboarding_api = (ROOT / "frontend/features/onboarding/onboarding-api.ts").read_text(
        encoding="utf-8"
    )

    assert "submitOnboarding(request)" in source
    assert 'postRequest<OnboardingInitializationResponse>("/api/onboarding/initialize", request)' in onboarding_api
    assert 'postRequest<GoalResponse>("/api/goals"' not in source


def test_frontend_handles_empty_today_tasks_without_dereferencing_current_task():
    provider = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")
    pages = (ROOT / "frontend/components/learning-pages.tsx").read_text(encoding="utf-8")
    shell = (ROOT / "frontend/components/learning-shell.tsx").read_text(encoding="utf-8")

    assert "currentTask: Task | null;" in provider
    assert "state.today_tasks[0] || null" in provider
    assert 'if (!currentTask) {' in provider
    assert 'displayTask?.title || t("page.currentNodeFallback")' in pages
    assert 'data-testid="empty-task-list"' in shell


def test_frontend_root_redirect_is_configured_at_the_server_boundary():
    config = (ROOT / "frontend/next.config.mjs").read_text(encoding="utf-8")

    assert "async redirects()" in config
    assert 'source: "/"' in config
    assert 'destination: "/path"' in config
    assert "permanent: false" in config


def test_identity_bound_requests_use_an_epoch_guard_and_synchronous_busy_lock():
    provider = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")
    shell = (ROOT / "frontend/components/learning-shell.tsx").read_text(encoding="utf-8")

    assert "identityEpochRef" in provider
    assert "isCurrentIdentity" in provider
    assert "busyKeysRef" in provider
    assert "busyKeysRef.current.has(key)" in provider
    assert "busyActionsRef" in provider
    assert "queueIfBusy" in provider
    assert 'runBusy("refresh"' in provider
    assert "{ queueIfBusy: true }" in provider
    assert 'setNote((current) => (current.trim() === content ? "" : current))' in provider
    assert 'id="profile-user-id"' not in shell
    assert "X-User-Id" not in shell
