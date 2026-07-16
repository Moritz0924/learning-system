import { expect, test } from "@playwright/test";

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

  const initialized = page.waitForResponse((response) =>
    response.url().includes("/api/onboarding/initialize") && response.request().method() === "POST"
  );
  await page.getByTestId("create-learning-path").click();
  expect((await initialized).status()).toBe(201);
  await expect(page).toHaveURL(/\/path$/);
  await expect(page.getByTestId("task-row").first()).toBeVisible();

  const refreshStatus = await page.evaluate(async (url) => (await fetch(url, { method: "POST", credentials: "include" })).status, `${apiBase}/api/auth/refresh`);
  expect(refreshStatus).toBe(200);

  await page.getByTitle("账户").click();
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/login/);
});
