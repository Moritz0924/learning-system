import { expect, test, type Page } from "@playwright/test";

import { fillDiagnosis, registerForDiagnosis } from "./onboarding-helpers";

const assessmentPayload = {
  assessment_id: "assessment-v2-e2e",
  assessment_type: "daily",
  status: "active",
  scope: { knowledge_node_ids: ["node-fastapi_basics"] },
  items: [
    {
      item_id: "item-choice",
      knowledge_node_id: "node-fastapi_basics",
      question_type: "choice",
      prompt: "Choose the safe request strategy.",
      options: [
        { option_id: "option-a", label: "Use a stable request ID" },
        { option_id: "option-b", label: "Generate a new ID for every retry" },
      ],
      difficulty: 2,
    },
    {
      item_id: "item-scenario",
      knowledge_node_id: "node-fastapi_basics",
      question_type: "scenario",
      prompt: "Describe how a network retry should be handled.",
      options: [],
      difficulty: 3,
    },
  ],
};

async function openAssessment(page: Page, name: string) {
  await registerForDiagnosis(page, name);
  await fillDiagnosis(page);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await page.goto("/assessment");
  await expect(page.getByTestId("assessment-create")).toBeVisible();
}

test("replays creation after a 401 with its original UUID and prevents duplicate clicks", async ({ page }) => {
  await openAssessment(page, "assessment-v2-refresh");
  const requestIds: string[] = [];
  let attempt = 0;
  await page.route("**/api/assessments", async (route) => {
    attempt += 1;
    requestIds.push(route.request().postDataJSON().request_id);
    if (attempt === 1) {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: { code: "auth.invalid_access_token", message: "expired" } }),
      });
      return;
    }
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(assessmentPayload) });
  });

  await page.getByTestId("assessment-create").dblclick();
  await expect(page.getByTestId("assessment-item-choice")).toBeVisible();

  expect(requestIds).toHaveLength(2);
  expect(new Set(requestIds).size).toBe(1);
});

test("retains the creation UUID after a network failure and renders public V2 grading states", async ({ page }) => {
  await openAssessment(page, "assessment-v2-network");
  const requestIds: string[] = [];
  let creationAttempt = 0;
  await page.route("**/api/assessments", async (route) => {
    creationAttempt += 1;
    requestIds.push(route.request().postDataJSON().request_id);
    if (creationAttempt === 1) {
      await route.abort("connectionfailed");
      return;
    }
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(assessmentPayload) });
  });

  await page.getByTestId("assessment-create").click();
  await expect(page.getByTestId("assessment-create")).toBeEnabled();
  await page.getByTestId("assessment-create").click();
  await expect(page.getByTestId("assessment-item-choice")).toBeVisible();
  await expect(page.getByTestId("assessment-item-scenario")).toBeVisible();
  await expect(page.locator('input[type="radio"]')).toHaveCount(2);

  expect(requestIds).toHaveLength(2);
  expect(new Set(requestIds).size).toBe(1);

  await page.getByRole("radio", { name: "Use a stable request ID" }).check();
  await page.getByTestId("assessment-answer-scenario").fill("Keep the same UUID and replay the same answers.");
  await page.route("**/api/assessments/assessment-v2-e2e/submit", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        assessment_id: "assessment-v2-e2e",
        attempt_id: "attempt-v2-e2e",
        status: "review_required",
        score: null,
        feedback: "An instructor needs to review this explanation.",
        grading: {
          mode: "manual_review_required",
          grader_version: "assessment-grading-policy-v2",
          confidence: 0,
          needs_review: true,
          automatic_mastery_eligible: false,
        },
        answers: [],
        mastery_updates: [],
        observer_decision: {
          policy_version: "observer-policy-v2",
          decision: "manual_review",
          automation_allowed: false,
          confidence: 0,
          reason_codes: ["manual_review_required"],
          user_facing_rationale: "Wait for instructor review.",
        },
        plan_adjustment: {
          adjustment_id: "adjustment-v2-e2e",
          decision: "manual_review",
          status: "proposed",
          automation_allowed: false,
          change_summary: { no_change: true },
          rationale: "No automatic adjustment is available.",
        },
      }),
    });
  });

  await page.getByTestId("assessment-submit").click();
  await expect(page.getByTestId("assessment-result")).toContainText("需要人工复核");
  await expect(page.getByTestId("assessment-plan-proposal")).toContainText("需要你的确认");
  const pageMarkup = await page.content();
  expect(pageMarkup).not.toContain("reference_answer");
  expect(pageMarkup).not.toContain("rubric_json");
});
