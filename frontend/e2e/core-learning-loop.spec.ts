import { expect, test, type Route } from "@playwright/test";

import {
  diagnosticDraft,
  fillDiagnosis,
  installDynamicOnboardingFixture,
  stateFixture,
} from "./onboarding-helpers";


function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function sse(type: string, data: Record<string, unknown>) {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

test("completes the protected deep-link learning loop through stale-task rejection", async ({ page, request }) => {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8123";
  const email = `core-loop-${Date.now().toString(36)}@example.com`;
  const password = "correct horse battery staple";
  const registration = await request.post(`${apiBase}/api/auth/register`, {
    headers: { Origin: "http://127.0.0.1:3100" },
    data: { email, password, display_name: "Core Loop Learner" },
  });
  expect(registration.status()).toBe(201);
  await installDynamicOnboardingFixture(page);

  await page.goto("/diagnosis?entry=core-loop");
  await expect(page).toHaveURL(/\/login\?next=%2Fdiagnosis%3Fentry%3Dcore-loop$/);
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/diagnosis\?entry=core-loop$/);

  await fillDiagnosis(page);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);

  const taskB = {
    id: "task-e2e-2",
    title: "Run release checks",
    objective: "Verify the assistant before release.",
    task_type: "study",
    scheduled_date: "2026-08-21",
    estimated_minutes: 40,
    status: "pending",
    knowledge_node_id: "node-release-checks",
    knowledge_node_code: "release_checks",
    knowledge_node_title: "Release checks",
  };
  let currentState = {
    ...stateFixture,
    today_tasks: [...stateFixture.today_tasks, taskB],
  };
  await page.route("**/api/state/current**", (route) => json(route, currentState));
  let transcriptMessages: Array<Record<string, unknown>> = [];
  await page.route("**/api/tutor/conversations/*/messages**", (route) => json(route, {
    messages: transcriptMessages,
    next_before: null,
  }));
  await page.route("**/api/tasks/task-e2e-2/start", (route) => json(route, {
    task: { ...taskB, status: "active" },
  }));

  await page.goto("/path?node=release-checks");
  await expect(page.getByRole("heading", { name: "Run release checks" })).toBeVisible();
  await page.getByTitle("收藏节点").click();
  await expect(page.getByTitle("取消收藏")).toBeVisible();
  await page.locator("header").getByRole("button", { name: "开始学习" }).click();
  await expect(page).toHaveURL(/\/tutor\?task=task-e2e-2$/);

  let tutorTaskId: unknown;
  await page.route("**/api/tutor/chat/stream", (route) => {
    const payload = route.request().postDataJSON();
    tutorTaskId = payload.task_id;
    transcriptMessages = [
      { id: "run-core:user", run_id: "run-core", role: "user", content: payload.message, created_at: "2026-09-02T08:00:00Z" },
      { id: "run-core:assistant", run_id: "run-core", role: "assistant", content: "Node B grounded answer", created_at: "2026-09-02T08:00:01Z", citations: [] },
    ];
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sse("run.started", { run_id: "run-core", thread_id: "thread-e2e" })
        + sse("run.completed", {
          result: {
            final_answer: "Node B grounded answer",
            citations: [],
            runtime_metadata: { rag: { citation_count: 0 } },
          },
        }),
    });
  });
  await page.getByTestId("tutor-question").fill("Explain Node B");
  await page.getByTestId("tutor-submit").click();
  await expect(page.getByTestId("tutor-answer")).toHaveText("Node B grounded answer");
  expect(tutorTaskId).toBe("task-e2e-2");

  await page.reload();
  await expect(page.getByTestId("tutor-transcript")).toContainText("Explain Node B");
  await expect(page.getByTestId("tutor-transcript")).toContainText("Node B grounded answer");
  await page.goto("/path?node=release-checks");
  await expect(page.getByTitle("取消收藏")).toBeVisible();
  await page.reload();
  await expect(page.getByTitle("取消收藏")).toBeVisible();

  let reassessedGoalId: unknown;
  await page.route("**/api/onboarding/reassess-drafts", (route) => json(route, diagnosticDraft, 201));
  await page.route("**/api/onboarding/reassess-from-draft", (route) => {
    reassessedGoalId = route.request().postDataJSON().goal_id;
    currentState = {
      ...currentState,
      active_plan: { id: "plan-e2e-2", version: 2 },
    };
    return json(route, {
      goal: { user_id: "user-e2e", goal_id: "goal-e2e", status: "active" },
      diagnosis: { entry_node_code: "retrieval_basics", active_plan_version: 2 },
      state: currentState,
      replayed: false,
    }, 201);
  });
  await page.getByRole("link", { name: "重新评估并生成专属路线" }).click();
  await expect(page).toHaveURL(/\/diagnosis\?mode=reassess&goal_id=goal-e2e$/);
  await page.getByTestId("reassess-generate-draft").click();
  const questions = page.getByTestId("knowledge-question");
  await expect(questions.first()).toBeVisible();
  for (let index = 0; index < await questions.count(); index += 1) {
    await questions.nth(index).locator('input[type="radio"]').first().check();
  }
  await page.getByTestId("reassess-submit").click();
  await expect(page).toHaveURL(/\/path$/);
  expect(reassessedGoalId).toBe("goal-e2e");

  let staleTaskId: unknown;
  await page.route("**/api/tutor/chat/stream", (route) => {
    staleTaskId = route.request().postDataJSON().task_id;
    return json(route, {
      detail: {
        code: "tutor.task_context_mismatch",
        message: "task is not part of the active learning plan",
      },
    }, 409);
  });
  await page.goto("/tutor?task=task-e2e-2");
  await page.getByTestId("tutor-question").fill("Explain the old Node B task");
  await page.getByTestId("tutor-submit").click();
  await expect(page.getByTestId("tutor-failure")).toContainText("该任务属于旧学习计划，请返回当前学习路径。");
  expect(staleTaskId).toBe("task-e2e-2");
});
