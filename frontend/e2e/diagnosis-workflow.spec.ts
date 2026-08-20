import { expect, test } from "@playwright/test";

import {
  diagnosticDraft,
  fillDiagnosis,
  fillGoalAndPreferences,
  registerForDiagnosis,
} from "./onboarding-helpers";


test("creates locale-aware dynamic questions and initializes from the draft", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-dynamic");
  const draftRequestPromise = page.waitForRequest((request) =>
    request.url().includes("/api/onboarding/dynamic-drafts")
  );
  await fillDiagnosis(page);
  const draftPayload = (await draftRequestPromise).postDataJSON();
  expect(draftPayload.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(draftPayload.locale).toBe("zh-CN");
  expect(draftPayload.goal.title).toBe("Build a dependable AI learning assistant");
  expect(draftPayload.goal.learning_preferences.explanation_order).toEqual(["analogy", "principle"]);

  const initializeRequestPromise = page.waitForRequest((request) =>
    request.url().includes("/api/onboarding/initialize-from-draft")
  );
  await page.getByTestId("create-learning-path").click();
  const initializePayload = (await initializeRequestPromise).postDataJSON();
  expect(initializePayload.user_id).toBeUndefined();
  expect(initializePayload.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(initializePayload.draft_id).toBe(diagnosticDraft.draft_id);
  expect(initializePayload.knowledge_answers).toHaveLength(3);
  await expect(page).toHaveURL(/\/path$/);
  const serverRoadmap = page.getByTestId("server-roadmap");
  await expect(serverRoadmap.getByText("Retrieval foundations", { exact: true })).toBeVisible();
  await expect(serverRoadmap.getByText("Trace retrieval evidence", { exact: true })).toBeVisible();
});


test("double click sends only one draft initialization request", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-double");
  await fillDiagnosis(page);
  let requestCount = 0;
  await page.route("**/api/onboarding/initialize-from-draft", async (route) => {
    requestCount += 1;
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fallback();
  });

  await page.getByTestId("create-learning-path").dblclick();
  await expect(page).toHaveURL(/\/path$/);
  expect(requestCount).toBe(1);
});


test("does not initialize while a required dynamic answer is missing", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-required");
  await fillDiagnosis(page, { answerKnowledge: false });
  let requestCount = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/onboarding/initialize-from-draft")) requestCount += 1;
  });

  await page.getByTestId("create-learning-path").click();

  await expect(page.getByText("请回答全部知识诊断题后再提交。", { exact: true })).toBeVisible();
  expect(requestCount).toBe(0);
});


test("draft network retry preserves goal and preferences and reuses its request id", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-draft-retry");
  const requestIds: string[] = [];
  let attempt = 0;
  await page.route("**/api/onboarding/dynamic-drafts", async (route) => {
    attempt += 1;
    requestIds.push(route.request().postDataJSON().request_id);
    if (attempt === 1) return route.abort("connectionfailed");
    return route.fallback();
  });

  await page.getByTestId("goal-title").fill("Build a dependable AI learning assistant");
  await page
    .getByTestId("target-outcome")
    .fill("Ship an authenticated AI tutor with tested retrieval and evaluation workflows.");
  await page.getByTestId("goal-deadline").fill("2026-12-20");
  await page.getByTestId("diagnosis-next").click();
  await page.getByTestId("weekly-hours").fill("9");
  await page.getByTestId("preference-analogy").click();
  await page.getByTestId("preferred-session-minutes").fill("50");
  await page.getByTestId("diagnosis-next").click();
  await expect(page.getByTestId("diagnosis-next")).toBeEnabled();

  await page.getByTestId("diagnosis-previous").click();
  await expect(page.getByTestId("goal-title")).toHaveValue("Build a dependable AI learning assistant");
  await page.getByTestId("diagnosis-next").click();
  await expect(page.getByTestId("weekly-hours")).toHaveValue("9");
  await expect(page.getByTestId("preference-analogy")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("diagnosis-next").click();
  await expect(page.getByTestId("dynamic-diagnostic-ready")).toBeVisible();

  expect(requestIds).toHaveLength(2);
  expect(new Set(requestIds).size).toBe(1);
});


test("401 and manual initialization retries reuse the same request id and answers", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-initialize-retry");
  await fillDiagnosis(page);
  const requestIds: string[] = [];
  const answerSnapshots: string[] = [];
  let attempt = 0;
  await page.route("**/api/onboarding/initialize-from-draft", async (route) => {
    attempt += 1;
    const payload = route.request().postDataJSON();
    requestIds.push(payload.request_id);
    answerSnapshots.push(JSON.stringify(payload.knowledge_answers));
    if (attempt === 1) {
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: { code: "auth.invalid_access_token" } }),
      });
    }
    if (attempt === 2) return route.abort("connectionfailed");
    return route.fallback();
  });

  await page.getByTestId("create-learning-path").click();
  await expect.poll(() => requestIds.length).toBe(2);
  await expect(page.getByTestId("create-learning-path")).toBeEnabled();
  await expect(page.getByTestId("knowledge-question").first().locator('input[type="radio"]:checked')).toHaveCount(1);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);

  expect(requestIds).toHaveLength(3);
  expect(new Set(requestIds).size).toBe(1);
  expect(new Set(answerSnapshots).size).toBe(1);
});


test("dynamic model failure offers AI configuration without static questions", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-model-failure");
  await page.route("**/api/onboarding/dynamic-drafts", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({
      detail: {
        code: "onboarding.dynamic_model_unavailable",
        message: "unsafe provider body must not be shown",
      },
    }),
  }));

  await page.getByTestId("goal-title").fill("Build a dependable AI learning assistant");
  await page
    .getByTestId("target-outcome")
    .fill("Ship an authenticated AI tutor with tested retrieval and evaluation workflows.");
  await page.getByTestId("diagnosis-next").click();
  await page.getByTestId("weekly-hours").fill("9");
  await page.getByTestId("preference-analogy").click();
  await page.getByTestId("preferred-session-minutes").fill("50");
  await page.getByTestId("diagnosis-next").click();

  await expect(page.getByRole("alert").filter({ hasText: "暂时无法生成专属诊断题" })).toBeVisible();
  await expect(page.getByRole("link", { name: "前往 AI 配置" })).toHaveAttribute("href", "/ai-config");
  await expect(page.getByText("unsafe provider body must not be shown")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-question")).toHaveCount(0);
});


test("legacy null roadmap shows reassessment instead of the old fixed path", async ({ page }) => {
  const fixture = await registerForDiagnosis(page, "diagnosis-legacy-roadmap");
  fixture.setRoadmap(null);
  await fillGoalAndPreferences(page);
  const questions = page.getByTestId("knowledge-question");
  for (let index = 0; index < (await questions.count()); index += 1) {
    await questions.nth(index).locator('input[type="radio"]').first().check();
  }
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await expect(page.getByRole("link", { name: "重新评估并生成专属路线" }).first()).toHaveAttribute("href", "/diagnosis");
  await expect(page.getByText("模型选择与提示工程", { exact: true })).toHaveCount(0);
});
