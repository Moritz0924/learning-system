import { expect, test, type Page } from "@playwright/test";

import { fillDiagnosis, registerForDiagnosis } from "./onboarding-helpers";


const chatResponse = {
  route: "teaching",
  final_answer: "Memory-aware answer",
  citations: [],
  runtime_metadata: {
    llm: { mode: "test", is_remote: false, model: "e2e" },
    rag: { mode: "live", citation_count: 0, fallback_citations: false },
    memory: { selected_count: 0, skipped_by_budget: 0, policy_version: "memory-context-v1" },
    memory_write: {
      candidate_count: 1,
      approved_count: 1,
      saved_count: 1,
      rejected_count: 0,
      conflict_count: 0,
      policy_version: "memory-gate-v1",
    },
  },
  assessment_draft: null,
  assessment_result: null,
  mastery_updates: [],
  observer_decision: null,
  plan_adjustment: null,
  audit_log: [],
};

const chatStreamBody = [
  `event: run.started\ndata: ${JSON.stringify({ run_id: "run-e2e", thread_id: "thread-e2e" })}\n\n`,
  `event: teacher.delta\ndata: ${JSON.stringify({ delta: chatResponse.final_answer })}\n\n`,
  `event: run.completed\ndata: ${JSON.stringify({ result: chatResponse })}\n\n`,
].join("");

async function initializeTutor(page: Page, prefix: string) {
  await registerForDiagnosis(page, prefix);
  await fillDiagnosis(page);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await page.goto("/tutor");
  await expect(page.getByTestId("memory-declaration-toggle")).toBeEnabled();
}

async function fillPreferenceDeclaration(page: Page) {
  await page.getByTestId("tutor-question").fill("Explain retrieval with an example.");
  await page.getByTestId("memory-declaration-toggle").check();
  await page.getByTestId("memory-preference-key").fill("explanation_style");
  await page.getByTestId("memory-preference-value").fill("examples_first");
}

test("quick chat never declares memory and main tutor sends a browser UUID", async ({ page }) => {
  await initializeTutor(page, "memory-structure");
  const sessionSelect = page.getByLabel("Tutor session");
  await expect(sessionSelect.locator("option")).toHaveCount(1);
  await page.getByRole("button", { name: "New session" }).click();
  await expect(sessionSelect.locator("option")).toHaveCount(2);
  await page.getByRole("button", { name: "Delete session" }).click();
  await expect(sessionSelect.locator("option")).toHaveCount(1);
  const requests: Array<Record<string, unknown>> = [];
  await page.route("**/api/tutor/chat/stream", async (route) => {
    requests.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: chatStreamBody });
  });

  await page.getByLabel("追问讲师").fill("Quick question");
  await page.getByLabel("追问讲师").locator("..").getByRole("button").click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].memory_declaration).toBeUndefined();
  expect(requests[0].thread_id).toMatch(/^thread-[0-9a-f-]+$/i);

  await fillPreferenceDeclaration(page);
  await page.getByTestId("tutor-submit").click();
  await expect.poll(() => requests.length).toBe(2);
  const declaration = requests[1].memory_declaration as Record<string, unknown>;
  expect(declaration).toMatchObject({
    memory_type: "learning_preference",
    preference_key: "explanation_style",
    preference_value: "examples_first",
  });
  expect(declaration.request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  await expect(page.getByTestId("memory-declaration-toggle")).not.toBeChecked();
});

test("session controls lock from submit until the stream responds", async ({ page }) => {
  await initializeTutor(page, "stream-session-lock");
  let requestStarted = false;
  let releaseRoute: (() => void) | undefined;
  const routeReleased = new Promise<void>((resolve) => { releaseRoute = resolve; });
  await page.route("**/api/tutor/chat/stream", async (route) => {
    requestStarted = true;
    await routeReleased;
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: chatStreamBody });
  });

  await page.getByTestId("tutor-question").fill("Hold session controls");
  await page.getByTestId("tutor-submit").click();
  await expect.poll(() => requestStarted).toBe(true);
  await expect(page.getByLabel("Tutor session")).toBeDisabled();
  await expect(page.getByRole("button", { name: "New session" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Delete session" })).toBeDisabled();

  releaseRoute?.();
  await expect(page.getByLabel("Tutor session")).toBeEnabled();
});

test("401 authentication retry reuses the exact memory request UUID", async ({ page }) => {
  await initializeTutor(page, "memory-auth-retry");
  const requestIds: string[] = [];
  let calls = 0;
  await page.route("**/api/tutor/chat/stream", async (route) => {
    calls += 1;
    const body = route.request().postDataJSON();
    requestIds.push(body.memory_declaration.request_id);
    if (calls === 1) {
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: { code: "auth.invalid_access_token" } }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: chatStreamBody });
  });

  await fillPreferenceDeclaration(page);
  await page.getByTestId("tutor-submit").click();

  await expect.poll(() => requestIds.length).toBe(2);
  expect(requestIds[0]).toBe(requestIds[1]);
});

test("manual network retry reuses UUID and privacy master switch disables declaration", async ({ page }) => {
  await initializeTutor(page, "memory-network-retry");
  const requestIds: string[] = [];
  let calls = 0;
  await page.route("**/api/tutor/chat/stream", async (route) => {
    calls += 1;
    const body = route.request().postDataJSON();
    requestIds.push(body.memory_declaration.request_id);
    if (calls === 1) {
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  await fillPreferenceDeclaration(page);
  await page.getByTestId("tutor-submit").click();
  await expect.poll(() => requestIds.length).toBe(1);
  await expect(page.getByTestId("tutor-submit")).toBeEnabled();
  const response = page.waitForResponse((item) => item.url().includes("/api/tutor/chat/stream") && item.status() === 200);
  await page.getByTestId("tutor-submit").click();
  await (await response).finished();
  expect(requestIds[0]).toBe(requestIds[1]);

  await page.unroute("**/api/tutor/chat/stream");
  await page.goto("/settings");
  await expect(page.getByTestId("memory-row")).toHaveCount(1);
  await page.getByTestId("disable-memory").click();
  await expect(page.getByTestId("memory-row")).toHaveCount(0);
  const privacyResponse = page.waitForResponse((item) => item.url().includes("/api/memories/privacy") && item.request().method() === "PUT");
  await page.getByTestId("memory-privacy-enabled").uncheck();
  expect((await privacyResponse).status()).toBe(200);
  await page.goto("/tutor");
  await expect(page.getByTestId("memory-declaration-toggle")).toBeDisabled();
});

test("switching users clears the previous memory list", async ({ page }) => {
  await initializeTutor(page, "memory-identity-a");
  await fillPreferenceDeclaration(page);
  const saved = page.waitForResponse((item) => item.url().includes("/api/tutor/chat/stream") && item.status() === 200);
  await page.getByTestId("tutor-submit").click();
  await (await saved).finished();
  await page.goto("/settings");
  await expect(page.getByTestId("memory-row")).toHaveCount(1);

  await page.getByTitle("账户").click();
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/login/);
  await registerForDiagnosis(page, "memory-identity-b");
  await page.goto("/settings");

  await expect(page.getByTestId("memory-settings-panel")).toBeVisible();
  await expect(page.getByTestId("memory-row")).toHaveCount(0);
});
