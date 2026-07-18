import { expect, test } from "@playwright/test";

import { fillDiagnosis, registerForDiagnosis } from "./onboarding-helpers";


test("loads a safe template and submits real user input", async ({ page }) => {
  const templateResponsePromise = page.waitForResponse((response) =>
    response.url().includes("/api/onboarding/diagnostic-template")
  );
  await registerForDiagnosis(page, "diagnosis-real");
  const templateResponse = await templateResponsePromise;
  const rawTemplate = await templateResponse.text();
  expect(rawTemplate).not.toContain("correct_option_id");
  expect(rawTemplate).not.toContain('"weight"');

  await fillDiagnosis(page);
  const requestPromise = page.waitForRequest((request) =>
    request.url().includes("/api/onboarding/initialize")
  );
  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/api/onboarding/initialize")
  );
  await page.getByTestId("create-learning-path").click();
  const request = await requestPromise;
  const payload = request.postDataJSON();
  expect(payload.user_id).toBeUndefined();
  expect(payload.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(payload.goal.title).toBe("Build a dependable AI learning assistant");
  expect(payload.goal.deadline).toBe("2026-12-20");
  expect(payload.goal.weekly_hours_target).toBe(9);
  expect(payload.self_assessment_answers.length).toBeGreaterThan(0);
  expect(payload.knowledge_answers.length).toBeGreaterThan(0);
  expect((await responsePromise).status()).toBe(201);
  await expect(page).toHaveURL(/\/path$/);
});


test("double click sends only one initialization request", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-double");
  await fillDiagnosis(page);
  let requestCount = 0;
  await page.route("**/api/onboarding/initialize", async (route) => {
    requestCount += 1;
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.continue();
  });

  await page.getByTestId("create-learning-path").dblclick();
  await expect(page).toHaveURL(/\/path$/);
  expect(requestCount).toBe(1);
});


test("does not submit while a required knowledge answer is missing", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-required");
  await fillDiagnosis(page, { answerKnowledge: false });
  let requestCount = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/onboarding/initialize")) requestCount += 1;
  });

  await page.getByTestId("create-learning-path").click();

  await expect(page.getByText("请回答全部知识诊断题后再提交。", { exact: true })).toBeVisible();
  expect(requestCount).toBe(0);
});


test("401 refresh replay keeps the same request id", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-refresh");
  await fillDiagnosis(page);
  const requestIds: string[] = [];
  let attempt = 0;
  await page.route("**/api/onboarding/initialize", async (route) => {
    attempt += 1;
    requestIds.push(route.request().postDataJSON().request_id);
    if (attempt === 1) {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "auth.invalid_access_token",
            message: "Authentication credentials are invalid or expired.",
          },
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  expect(requestIds).toHaveLength(2);
  expect(new Set(requestIds).size).toBe(1);
});


test("failed network attempt keeps answers and manually replays the same request id", async ({ page }) => {
  await registerForDiagnosis(page, "diagnosis-replay");
  await fillDiagnosis(page);
  const requestIds: string[] = [];
  let attempt = 0;
  await page.route("**/api/onboarding/initialize", async (route) => {
    attempt += 1;
    requestIds.push(route.request().postDataJSON().request_id);
    if (attempt === 1) {
      await route.abort("connectionfailed");
      return;
    }
    await route.continue();
  });

  await page.getByTestId("create-learning-path").click();
  await expect(page.getByTestId("create-learning-path")).toBeEnabled();
  await page.getByTestId("diagnosis-previous").click();
  await expect(page.locator('[data-testid^="self-level-"][data-testid$="-2"]').first()).toHaveAttribute(
    "aria-pressed",
    "true"
  );
  await page.getByTestId("diagnosis-previous").click();
  await expect(page.getByTestId("weekly-hours")).toHaveValue("9");
  await expect(page.getByTestId("preference-analogy")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("code-first")).toBeChecked();
  await page.getByTestId("diagnosis-previous").click();
  await expect(page.getByTestId("goal-title")).toHaveValue("Build a dependable AI learning assistant");
  await expect(page.getByTestId("goal-deadline")).toHaveValue("2026-12-20");
  await page.getByTestId("diagnosis-next").click();
  await page.getByTestId("diagnosis-next").click();
  await page.getByTestId("diagnosis-next").click();
  await expect(page.getByTestId("knowledge-question").first().locator('input[type="radio"]:checked')).toHaveCount(1);

  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  expect(requestIds).toHaveLength(2);
  expect(new Set(requestIds).size).toBe(1);
});
