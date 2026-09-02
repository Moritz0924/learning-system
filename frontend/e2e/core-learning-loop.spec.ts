import { expect, test } from "@playwright/test";

import { fillDiagnosis } from "./onboarding-helpers";


test("completes the real protected learning loop through stale-task rejection", async ({ page, request }) => {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8123";
  const email = `core-loop-${Date.now().toString(36)}@example.com`;
  const password = "correct horse battery staple";
  const registration = await request.post(`${apiBase}/api/auth/register`, {
    headers: { Origin: "http://127.0.0.1:3100" },
    data: { email, password, display_name: "Core Loop Learner" },
  });
  expect(registration.status()).toBe(201);
  const accessToken = (await registration.json()).access_token as string;
  const apiHeaders = { Authorization: `Bearer ${accessToken}` };
  const model = await request.post(`${apiBase}/api/config/models`, {
    headers: apiHeaders,
    data: {
      name: "E2E Tutor",
      capability: "chat",
      provider: "openai_compatible",
      base_url: "http://127.0.0.1:8124/v1",
      model_name: "e2e-model",
      dimensions: null,
      enabled: true,
    },
  });
  expect(model.status()).toBe(201);
  const modelId = (await model.json()).id as string;
  expect((await request.put(`${apiBase}/api/config/models/${modelId}/secret`, {
    headers: apiHeaders,
    data: { value: "e2e-provider-key" },
  })).status()).toBe(200);
  expect((await request.put(`${apiBase}/api/config/bindings/chat`, {
    headers: apiHeaders,
    data: { model_profile_id: modelId },
  })).status()).toBe(200);

  const protectedNext = "/diagnosis?entry=core-loop&resource=a%2Fb&anchor=x%23y";
  await page.goto(protectedNext);
  await expect(page).toHaveURL(/\/login\?/);
  expect(new URL(page.url()).searchParams.get("next")).toBe(protectedNext);
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/diagnosis\?entry=core-loop&resource=a%2Fb&anchor=x%23y$/);

  await fillDiagnosis(page);
  const initialized = page.waitForResponse((response) =>
    response.url().includes("/api/onboarding/initialize-from-draft")
      && response.request().method() === "POST"
  );
  await page.getByTestId("create-learning-path").click();
  const initializedPayload = await (await initialized).json();
  const goalId = initializedPayload.goal.goal_id as string;
  await expect(page).toHaveURL(/\/path$/);
  await expect(page.getByRole("button", { name: /Node B/ })).toBeVisible();

  await page.goto("/path?node=node-b");
  await expect(page.getByRole("heading", { name: "Node B" })).toBeVisible();
  await page.getByTitle("收藏节点").click();
  await expect(page.getByTitle("取消收藏")).toBeVisible();
  await page.locator("header").getByRole("button", { name: "开始学习" }).click();
  await expect(page).toHaveURL(/\/tutor\?task=task-/);
  const taskB = new URL(page.url()).searchParams.get("task");
  expect(taskB).toBeTruthy();

  await page.getByTestId("tutor-question").fill("Explain Node B");
  const tutorResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/tutor/chat/stream")
      && response.request().method() === "POST"
  );
  await page.getByTestId("tutor-submit").click();
  expect((await tutorResponse).request().postDataJSON().task_id).toBe(taskB);
  await expect(page.getByTestId("tutor-answer")).toHaveText("节点 B 的可靠回答");

  await page.reload();
  await expect(page.getByTestId("tutor-transcript")).toContainText("Explain Node B");
  await expect(page.getByTestId("tutor-transcript")).toContainText("节点 B 的可靠回答");
  await page.goto("/path?node=node-b");
  await expect(page.getByTitle("取消收藏")).toBeVisible();
  await page.reload();
  await expect(page.getByTitle("取消收藏")).toBeVisible();
  const nodeBTask = page.getByTestId("task-row").filter({ hasText: "Node B" });
  const completed = page.waitForResponse((response) =>
    response.url().includes(`/api/tasks/${taskB}/complete`)
      && response.request().method() === "POST"
  );
  await nodeBTask.getByRole("button", { name: "完成" }).click();
  expect((await completed).status()).toBe(200);

  await page.locator(`a[href="/diagnosis?mode=reassess&goal_id=${goalId}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/diagnosis\\?mode=reassess&goal_id=${goalId}$`));
  await page.getByTestId("reassess-generate-draft").click();
  const questions = page.getByTestId("knowledge-question");
  await expect(questions.first()).toBeVisible();
  for (let index = 0; index < await questions.count(); index += 1) {
    await questions.nth(index).locator('input[type="radio"]').first().check();
  }
  const reassessed = page.waitForResponse((response) =>
    response.url().includes("/api/onboarding/reassess-from-draft")
      && response.request().method() === "POST"
  );
  await page.getByTestId("reassess-submit").click();
  const reassessedResponse = await reassessed;
  expect(reassessedResponse.status(), await reassessedResponse.text()).toBe(200);
  const reassessedPayload = await reassessedResponse.json();
  expect(reassessedPayload.goal.goal_id).toBe(goalId);
  await expect(page).toHaveURL(/\/path$/);

  await page.goto(`/tutor?task=${encodeURIComponent(taskB ?? "")}`);
  await page.getByTestId("tutor-question").fill("Explain the old Node B task");
  const staleResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/tutor/chat/stream")
      && response.request().method() === "POST"
  );
  await page.getByTestId("tutor-submit").click();
  expect((await staleResponse).status()).toBe(409);
  await expect(page.getByTestId("tutor-failure")).toContainText(
    "该任务属于旧学习计划，请返回当前学习路径。",
  );
});
