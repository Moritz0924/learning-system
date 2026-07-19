import { expect, type Page } from "@playwright/test";


export async function registerForDiagnosis(page: Page, emailPrefix: string) {
  const email = `${emailPrefix}-${Date.now().toString(36)}@example.com`;
  await page.goto("/register");
  await page.getByTestId("register-name").fill("E2E Learner");
  await page.getByTestId("register-email").fill(email);
  await page.getByTestId("register-password").fill("correct horse battery staple");
  await page.getByTestId("register-submit").click();
  await expect(page).toHaveURL(/\/diagnosis$/);
  await expect(page.getByTestId("diagnosis-template-ready")).toBeVisible();
}


export async function fillDiagnosis(page: Page, options: { answerKnowledge?: boolean } = {}) {
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

  const levelChoices = page.locator('[data-testid^="self-level-"][data-testid$="-2"]');
  for (let index = 0; index < (await levelChoices.count()); index += 1) {
    await levelChoices.nth(index).click();
  }
  await page.getByTestId("diagnosis-next").click();

  if (options.answerKnowledge !== false) {
    const questions = page.getByTestId("knowledge-question");
    for (let index = 0; index < (await questions.count()); index += 1) {
      await questions.nth(index).locator('input[type="radio"]').first().check();
    }
  }
}
