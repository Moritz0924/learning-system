import { expect, test } from "@playwright/test";

import { fillDiagnosis } from "./onboarding-helpers";

test("redirects anonymous learning routes to login", async ({ page }) => {
  await page.goto("/diagnosis");
  await expect(page).toHaveURL(/\/login\?next=%2Fdiagnosis$/);
});

test("registers, initializes onboarding, refreshes, and logs out", async ({ page }) => {
  const email = `e2e-${Date.now().toString(36)}@example.com`;
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8123";
  await page.goto("/register");
  await page.getByTestId("register-name").fill("E2E Learner");
  await page.getByTestId("register-email").fill(email);
  await page.getByTestId("register-password").fill("correct horse battery staple");
  await page.getByTestId("register-submit").click();
  await expect(page).toHaveURL(/\/diagnosis$/);
  await expect(page.getByTestId("diagnosis-template-ready")).toBeVisible();

  const initialized = page.waitForResponse((response) =>
    response.url().includes("/api/onboarding/initialize") && response.request().method() === "POST"
  );
  await fillDiagnosis(page);
  await page.getByTestId("create-learning-path").click();
  expect((await initialized).status()).toBe(201);
  await expect(page).toHaveURL(/\/path$/);
  await expect(page.getByTestId("task-row").first()).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("task-row").first()).toBeVisible();

  const refreshStatus = await page.evaluate(async (url) => (await fetch(url, { method: "POST", credentials: "include" })).status, `${apiBase}/api/auth/refresh`);
  expect(refreshStatus).toBe(200);

  await page.getByTitle("账户").click();
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/login/);
});

test("shows the server registration error without exposing raw response data", async ({ page }) => {
  const email = `duplicate-${Date.now().toString(36)}@example.com`;
  await page.goto("/register");
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await page.getByTestId("register-name").fill("Duplicate Learner");
    await page.getByTestId("register-email").fill(email);
    await page.getByTestId("register-password").fill("correct horse battery staple");
    await page.getByTestId("register-submit").click();
    if (attempt === 0) await page.goto("/register");
  }
  await expect(page.getByText("Email is already registered.", { exact: true })).toBeVisible();
});

test("restores concurrent tabs without persisting access tokens", async ({ page, context }) => {
  const email = `tabs-${Date.now().toString(36)}@example.com`;
  await page.goto("/register");
  await page.getByTestId("register-name").fill("Tab Learner");
  await page.getByTestId("register-email").fill(email);
  await page.getByTestId("register-password").fill("correct horse battery staple");
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("diagnosis-template-ready")).toBeVisible();
  await fillDiagnosis(page);
  await page.getByTestId("create-learning-path").click();
  await expect(page.getByTestId("task-row").first()).toBeVisible();

  const peer = await context.newPage();
  await Promise.all([page.reload(), peer.goto("/path")]);
  await Promise.all([
    expect(page.getByTestId("task-row").first()).toBeVisible(),
    expect(peer.getByTestId("task-row").first()).toBeVisible(),
  ]);
  const browserStorage = await page.evaluate(() => ({
    local: Object.entries(localStorage),
    session: Object.entries(sessionStorage),
  }));
  expect(browserStorage.local).toEqual([]);
  for (const [key, value] of browserStorage.session) {
    expect(key).toMatch(/^__next_debug_channel:/);
    expect(`${key}:${value}`).not.toMatch(/access[_-]?token|refresh[_-]?token|bearer\s+eyj|eyj[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+/i);
  }
  await peer.close();
});
