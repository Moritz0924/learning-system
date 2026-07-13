import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

function resolvePython() {
  const configured = process.env.E2E_PYTHON?.trim();
  if (configured) return configured;
  const virtualenvPython = path.resolve(
    process.cwd(),
    "../.venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
  );
  if (existsSync(virtualenvPython)) return virtualenvPython;
  return process.platform === "win32" ? "python" : "python3";
}

function moveUserTasksToTomorrow(userId: string) {
  const python = resolvePython();
  const script = [
    "import os",
    "from datetime import date, timedelta",
    "from sqlalchemy import create_engine, text",
    "engine = create_engine(os.environ['DATABASE_URL'])",
    "with engine.begin() as connection:",
    "    result = connection.execute(text('UPDATE plan_tasks SET scheduled_date = :scheduled_date WHERE user_id = :user_id'), {'scheduled_date': date.today() + timedelta(days=1), 'user_id': os.environ['E2E_USER_ID']})",
    "    assert result.rowcount > 0",
  ].join("\n");
  const result = spawnSync(python, ["-c", script], {
    encoding: "utf8",
    env: { ...process.env, E2E_USER_ID: userId }
  });
  expect(result.status, result.stderr || result.stdout).toBe(0);
}

test("redirects the root route at the HTTP layer", async ({ request }) => {
  const response = await request.get("/", { maxRedirects: 0 });

  expect(response.status()).toBe(307);
  expect(response.headers()["location"]).toBe("/path");
});

test("creates a learning path through real backend APIs", async ({ page }) => {
  const userId = `e2e-${Date.now().toString(36)}`;

  await page.goto("/diagnosis");
  await page.getByTestId("diagnosis-user-id").fill(userId);

  const initializeResponsePromise = page.waitForResponse(
    (response) => response.url().includes("/api/onboarding/initialize") && response.request().method() === "POST"
  );

  await page.getByTestId("create-learning-path").click();

  const initializeResponse = await initializeResponsePromise;
  expect(initializeResponse.status()).toBe(201);
  const initializePayload = (await initializeResponse.json()) as {
    goal?: { goal_id?: string; user_id?: string };
    state?: { today_tasks?: unknown[] };
  };
  const goalPayload = initializePayload.goal || {};
  expect(goalPayload.user_id).toBe(userId);
  expect(goalPayload.goal_id).toBeTruthy();
  const statePayload = initializePayload.state || {};
  expect(statePayload.today_tasks?.length).toBeGreaterThan(0);

  await expect(page).toHaveURL(/\/path$/);
  await expect(page.getByTestId("task-row").first()).toBeVisible();
});

test("keeps demo state coherent when atomic onboarding fails", async ({ page }) => {
  const userId = `e2e-failed-diagnosis-${Date.now().toString(36)}`;
  let initializeCalls = 0;
  await page.route("**/api/onboarding/initialize", async (route) => {
    initializeCalls += 1;
    await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"injected failure"}' });
  });
  await page.goto("/diagnosis");
  await page.getByTestId("diagnosis-user-id").fill(userId);

  await page.getByTestId("create-learning-path").click();

  await expect(page).toHaveURL(/\/diagnosis$/);
  await expect(page.getByTestId("demo-mode-banner")).toBeVisible();
  await expect(page.getByTestId("create-learning-path")).toBeEnabled();
  expect(initializeCalls).toBe(1);
});

test("renders a real empty today state after scheduled tasks move", async ({ page }) => {
  const userId = `e2e-empty-today-${Date.now().toString(36)}`;
  await page.goto("/diagnosis");
  await page.getByTestId("diagnosis-user-id").fill(userId);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await expect(page.getByTestId("task-row").first()).toBeVisible();

  moveUserTasksToTomorrow(userId);
  await page.locator('a[href="/today"]').click();
  await expect(page).toHaveURL(/\/today$/);
  await page.getByTestId("refresh-today-state").click();

  await expect(page.getByTestId("empty-task-list")).toBeVisible();
  await expect(page.getByTestId("task-row")).toHaveCount(0);
  await expect(page.getByTestId("primary-start-task")).toBeDisabled();
});

test("clears identity-bound state when the active user changes", async ({ page }) => {
  const firstUserId = `e2e-identity-a-${Date.now().toString(36)}`;
  const secondUserId = `e2e-identity-b-${Date.now().toString(36)}`;
  await page.goto("/diagnosis");
  await page.getByTestId("diagnosis-user-id").fill(firstUserId);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await expect(page.getByTestId("demo-mode-banner")).toHaveCount(0);
  await expect(page.getByTestId("task-row").first()).toContainText("Study ");

  await page.locator('a[href="/settings"]').click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByTestId("settings-user-id")).toHaveValue(firstUserId);
  const noteDraft = page.getByPlaceholder("把学习笔记保存为 markdown 资料...");
  const sourceQuery = page.getByLabel("官方来源检索");
  await noteDraft.fill("first-user-private-draft");
  await sourceQuery.fill("first-user-private-query");
  await page.getByTestId("settings-user-id").fill(secondUserId);
  await expect(noteDraft).toHaveValue("");
  await expect(sourceQuery).toHaveValue("FastAPI dependency injection");
  await page.locator('a[href="/path"]').click();
  await expect(page).toHaveURL(/\/path$/);

  await expect(page.getByTestId("demo-mode-banner")).toBeVisible();
  await expect(page.getByTestId("task-row").first()).toContainText("模型能力对比与选择策略");
  await expect(page.getByTestId("task-row").first()).not.toContainText("Study ");
});

test("ignores an onboarding response from an identity that was replaced in flight", async ({ page }) => {
  const firstUserId = `e2e-stale-a-${Date.now().toString(36)}`;
  const secondUserId = `e2e-stale-b-${Date.now().toString(36)}`;
  let releaseInitialize: (() => void) | undefined;
  let markInitializeStarted: (() => void) | undefined;
  const initializeStarted = new Promise<void>((resolve) => {
    markInitializeStarted = resolve;
  });
  const initializeReleased = new Promise<void>((resolve) => {
    releaseInitialize = resolve;
  });
  await page.route("**/api/onboarding/initialize", async (route) => {
    markInitializeStarted?.();
    await initializeReleased;
    await route.continue();
  });

  await page.goto("/diagnosis");
  const userInput = page.getByTestId("diagnosis-user-id");
  await userInput.fill(firstUserId);
  const initializeResponsePromise = page.waitForResponse(
    (response) => response.url().includes("/api/onboarding/initialize") && response.request().method() === "POST"
  );
  await page.getByTestId("create-learning-path").click();
  await initializeStarted;
  await userInput.evaluate((element) => element.removeAttribute("disabled"));
  await userInput.fill(secondUserId);
  releaseInitialize?.();
  const initializeResponse = await initializeResponsePromise;
  expect(initializeResponse.status()).toBe(201);
  const initializePayload = (await initializeResponse.json()) as { goal?: { user_id?: string } };
  expect(initializePayload.goal?.user_id).toBe(firstUserId);

  await expect(page).toHaveURL(/\/diagnosis$/);
  await expect(userInput).toHaveValue(secondUserId);
  await expect(page.getByTestId("demo-mode-banner")).toBeVisible();
  await expect(page.getByTestId("create-learning-path")).toBeEnabled();
});

test("queues a state refresh after a concurrent task mutation", async ({ page }) => {
  const userId = `e2e-refresh-queue-${Date.now().toString(36)}`;
  await page.goto("/diagnosis");
  await page.getByTestId("diagnosis-user-id").fill(userId);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await page.locator('a[href="/today"]').click();
  await expect(page).toHaveURL(/\/today$/);

  let stateRequestCount = 0;
  let releaseFirstRefresh: (() => void) | undefined;
  let markFirstRefreshCaptured: (() => void) | undefined;
  const firstRefreshCaptured = new Promise<void>((resolve) => {
    markFirstRefreshCaptured = resolve;
  });
  const firstRefreshReleased = new Promise<void>((resolve) => {
    releaseFirstRefresh = resolve;
  });
  await page.route("**/api/state/current**", async (route) => {
    stateRequestCount += 1;
    if (stateRequestCount !== 1) {
      await route.continue();
      return;
    }
    const staleResponse = await route.fetch();
    markFirstRefreshCaptured?.();
    await firstRefreshReleased;
    await route.fulfill({ response: staleResponse });
  });

  await page.getByTestId("refresh-today-state").click();
  await firstRefreshCaptured;
  const completeResponsePromise = page.waitForResponse(
    (response) => response.url().includes("/complete") && response.request().method() === "POST"
  );
  await page.getByTestId("task-row").first().getByRole("button", { name: "完成" }).click();
  const completeResponse = await completeResponsePromise;
  expect(completeResponse.status()).toBe(200);
  releaseFirstRefresh?.();

  await expect(page.getByTestId("task-row").first()).toContainText("已完成");
  await expect(page.getByTestId("task-row").first().getByRole("button", { name: "完成" })).toBeDisabled();
  expect(stateRequestCount).toBeGreaterThanOrEqual(2);
});

test("preserves a note edited while an earlier upload is in flight", async ({ page }) => {
  const userId = `e2e-note-race-${Date.now().toString(36)}`;
  await page.goto("/diagnosis");
  await page.getByTestId("diagnosis-user-id").fill(userId);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await page.locator('a[href="/settings"]').click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByTestId("settings-user-id")).toHaveValue(userId);

  let releaseUpload: (() => void) | undefined;
  let markUploadStarted: (() => void) | undefined;
  const uploadStarted = new Promise<void>((resolve) => {
    markUploadStarted = resolve;
  });
  const uploadReleased = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  await page.route("**/api/documents/upload", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    markUploadStarted?.();
    await uploadReleased;
    await route.continue();
  });

  const noteDraft = page.getByPlaceholder("把学习笔记保存为 markdown 资料...");
  await noteDraft.fill("submitted note");
  const uploadResponsePromise = page.waitForResponse(
    (response) => response.url().includes("/api/documents/upload") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "保存为资料" }).click();
  await uploadStarted;
  await noteDraft.fill("new note written during upload");
  releaseUpload?.();
  const uploadResponse = await uploadResponsePromise;
  expect(uploadResponse.status(), await uploadResponse.text()).toBe(201);

  await expect(noteDraft).toHaveValue("new note written during upload");
});
