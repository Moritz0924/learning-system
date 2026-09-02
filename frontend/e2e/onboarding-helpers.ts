import { expect, type Page, type Route } from "@playwright/test";


export const diagnosticDraft = {
  draft_id: "draft-e2e",
  expires_at: "2026-12-31T00:00:00+00:00",
  title: "AI assistant readiness check",
  questions: [1, 2, 3].map((number) => ({
    question_id: `dynamic-q-${number}`,
    prompt: `Dynamic diagnostic question ${number}?`,
    options: [
      { option_id: "a", label: `Answer A${number}` },
      { option_id: "b", label: `Answer B${number}` },
    ],
  })),
};

export const roadmapFixture = {
  title: "Dependable AI assistant roadmap",
  locale: "zh-CN",
  plan_version: 1,
  stages: [
    {
      stage_id: "foundation",
      title: "Retrieval foundations",
      objective: "Build a grounded retrieval baseline.",
      order: 1,
      status: "current",
      progress: 0.35,
      nodes: [
        {
          node_id: "retrieval-basics",
          knowledge_node_id: "node-retrieval-basics",
          task_id: "task-e2e-1",
          title: "Trace retrieval evidence",
          objective: "Explain where each answer claim comes from.",
          order: 1,
          status: "current",
          progress: 0.35,
        },
      ],
    },
    {
      stage_id: "delivery",
      title: "Reliable delivery",
      objective: "Ship and verify the assistant.",
      order: 2,
      status: "locked",
      progress: 0,
      nodes: [
        {
          node_id: "release-checks",
          knowledge_node_id: "node-release-checks",
          task_id: "task-e2e-2",
          title: "Run release checks",
          objective: "Verify the assistant before release.",
          order: 1,
          status: "locked",
          progress: 0,
        },
      ],
    },
  ],
};

export const stateFixture = {
  user_id: "user-e2e",
  goal: { id: "goal-e2e", title: "Build a dependable AI learning assistant" },
  active_plan: { id: "plan-e2e", version: 1 },
  baseline_diagnostic: { id: "diagnostic-e2e" },
  mastery_summary: [
    { label: "Retrieval basics", score: 35, confidence: 0.8, evidence_count: 1 },
  ],
  current_state: { review_queue: [], next_action: "study" },
  generated_from: { source: "dynamic_roadmap" },
  latest_plan_adjustment: null,
  today_tasks: [
    {
      id: "task-e2e-1",
      title: "Trace retrieval evidence",
      objective: "Explain where each answer claim comes from.",
      task_type: "study",
      scheduled_date: "2026-08-20",
      estimated_minutes: 45,
      status: "active",
      knowledge_node_id: "node-retrieval-basics",
      knowledge_node_code: "retrieval_basics",
      knowledge_node_title: "Retrieval basics",
    },
  ],
  updated_at: "2026-08-20T00:00:00+00:00",
  roadmap: roadmapFixture,
};

export type DynamicOnboardingFixture = {
  isInitialized: () => boolean;
  setRoadmap: (roadmap: typeof roadmapFixture | null) => void;
  setLatestPlanAdjustment: (adjustment: Record<string, unknown> | null) => void;
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function installDynamicOnboardingFixture(page: Page): Promise<DynamicOnboardingFixture> {
  let initialized = false;
  let roadmap: typeof roadmapFixture | null = roadmapFixture;
  let latestPlanAdjustment: Record<string, unknown> | null = null;
  const savedLearningNodes = new Set<string>();

  await page.route("**/api/onboarding/dynamic-drafts", (route) => json(route, diagnosticDraft, 201));
  await page.route("**/api/onboarding/initialize-from-draft", (route) => {
    initialized = true;
    return json(route, {
      goal: { user_id: "user-e2e", goal_id: "goal-e2e", status: "active" },
      diagnosis: { entry_node_code: "retrieval_basics", active_plan_version: 1 },
      state: { ...stateFixture, roadmap, latest_plan_adjustment: latestPlanAdjustment },
      replayed: false,
    }, 201);
  });
  await page.route("**/api/goals", async (route) => {
    if (route.request().method() !== "GET" || !initialized) return route.fallback();
    return json(route, {
      goals: [{
        goal_id: "goal-e2e",
        title: "Build a dependable AI learning assistant",
        target_outcome: "Ship an authenticated AI tutor with tested retrieval workflows.",
        deadline: "2026-12-20",
        weekly_hours_target: 9,
        status: "active",
        created_at: "2026-08-20T00:00:00+00:00",
      }],
    });
  });
  await page.route("**/api/state/current**", (route) => json(route, {
    ...stateFixture,
    roadmap,
    latest_plan_adjustment: latestPlanAdjustment,
  }));
  await page.route("**/api/saved-learning-nodes**", (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      return json(route, { knowledge_node_ids: [...savedLearningNodes] });
    }
    const nodeId = decodeURIComponent(new URL(request.url()).pathname.split("/").at(-1) ?? "");
    if (request.method() === "PUT") savedLearningNodes.add(nodeId);
    if (request.method() === "DELETE") savedLearningNodes.delete(nodeId);
    return route.fulfill({ status: 204 });
  });
  await page.route("**/api/tutor/conversations**", (route) => {
    if (route.request().method() === "GET") {
      return json(route, { conversations: [{
        thread_id: "thread-e2e",
        goal_id: "goal-e2e",
        title: "Tutor session",
        status: "active",
        created_at: "2026-08-20T00:00:00+00:00",
        updated_at: "2026-08-20T00:00:00+00:00",
      }] });
    }
    return json(route, {
      thread_id: `thread-${crypto.randomUUID()}`,
      goal_id: "goal-e2e",
      title: "Tutor session",
      status: "active",
      created_at: "2026-08-20T00:00:00+00:00",
      updated_at: "2026-08-20T00:00:00+00:00",
    }, 201);
  });
  await page.route("**/api/tutor/conversations/*/messages**", (route) => json(route, {
    messages: [],
    next_before: null,
  }));
  await page.route("**/api/tutor/tool-approvals**", (route) => json(route, { approvals: [] }));
  await page.route("**/api/tools/search-learning-sources", (route) => json(route, {
    results: [{
      title: "External retrieval guide",
      url: "https://example.test/retrieval-guide",
      snippet: "Verify this external source before using it.",
      retrieved_at: "2026-08-20T00:00:00+00:00",
      source_level: "web",
      retrieval_mode: "brave_search",
      is_live_search: true,
      trust_label: "external_unverified",
    }],
  }));

  return {
    isInitialized: () => initialized,
    setRoadmap: (value) => { roadmap = value; },
    setLatestPlanAdjustment: (value) => { latestPlanAdjustment = value; },
  };
}

export async function registerForDiagnosis(page: Page, emailPrefix: string) {
  const fixture = await installDynamicOnboardingFixture(page);
  const email = `${emailPrefix}-${Date.now().toString(36)}@example.com`;
  const password = "correct horse battery staple";
  await page.goto("/register");
  await page.getByTestId("register-name").fill("E2E Learner");
  await page.getByTestId("register-email").fill(email);
  await page.getByTestId("register-password").fill(password);
  await page.getByTestId("register-submit").click();
  await expect(page).toHaveURL(/\/diagnosis$/);
  await expect(page.getByTestId("diagnosis-form-ready")).toBeVisible();
  return { ...fixture, email, password };
}

export async function fillGoalAndPreferences(page: Page) {
  await page.getByTestId("goal-title").fill("Build a dependable AI learning assistant");
  await page
    .getByTestId("target-outcome")
    .fill("Ship an authenticated AI tutor with tested retrieval and evaluation workflows.");
  await page.getByTestId("goal-deadline").fill("2026-12-20");
  await page.getByTestId("diagnosis-next").click();

  await page.getByTestId("weekly-hours").fill("9");
  await page.getByTestId("preference-analogy").click();
  await page.getByTestId("preference-principle").click();
  await page.getByTestId("preferred-session-minutes").fill("50");
  await page.getByTestId("code-first").check();
  await page.getByTestId("diagnosis-next").click();
  await expect(page.getByTestId("dynamic-diagnostic-ready")).toBeVisible();
}

export async function fillDiagnosis(page: Page, options: { answerKnowledge?: boolean } = {}) {
  await fillGoalAndPreferences(page);
  if (options.answerKnowledge !== false) {
    const questions = page.getByTestId("knowledge-question");
    for (let index = 0; index < (await questions.count()); index += 1) {
      await questions.nth(index).locator('input[type="radio"]').first().check();
    }
  }
}
