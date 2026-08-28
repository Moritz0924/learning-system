import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

import { expect, test, type Page } from "@playwright/test";

import { fillDiagnosis, registerForDiagnosis } from "./onboarding-helpers";


const finalResult = {
  final_answer: "Server final answer",
  citations: [{ citation_label: "[1]", source_url: "https://example.test/evidence" }],
  runtime_metadata: {
    llm: { mode: "remote", is_remote: true, model: "fixture-model" },
    rag: { mode: "live", citation_count: 1, fallback_citations: false },
  },
};

function sse(type: string, data: Record<string, unknown>) {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

const corsHeaders = {
  "Access-Control-Allow-Credentials": "true",
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Origin": "http://127.0.0.1:3100",
};

function handlePreflight(request: IncomingMessage, response: ServerResponse) {
  if (request.method !== "OPTIONS") return false;
  response.writeHead(204, corsHeaders);
  response.end();
  return true;
}

async function initializeTutor(page: Page, prefix: string) {
  await registerForDiagnosis(page, prefix);
  await fillDiagnosis(page);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await page.goto("/tutor");
  await expect(page.getByLabel("讲师会话")).toHaveValue("thread-e2e");
}

async function startStreamingFixture(): Promise<{ server: Server; url: string }> {
  const server = createServer((request, response) => {
    if (handlePreflight(request, response)) return;
    response.writeHead(200, {
      ...corsHeaders,
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
    });
    response.write(sse("run.started", { run_id: "run-stream", thread_id: "thread-e2e" }));
    setTimeout(() => response.write(sse("node.started", { node: "retrieval" })), 300);
    setTimeout(() => response.write(sse("retrieval.completed", { citation_count: 1 })), 600);
    setTimeout(() => response.write(sse("node.started", { node: "teacher" })), 900);
    setTimeout(() => response.write(sse("teacher.delta", { delta: "First " })), 1_200);
    setTimeout(() => response.write(sse("teacher.delta", { delta: "answer" })), 1_500);
    setTimeout(() => {
      response.end(sse("run.completed", { result: finalResult }));
    }, 1_900);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  return { server, url: `http://127.0.0.1:${address.port}/stream` };
}

async function closeServer(server: Server) {
  server.closeAllConnections();
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}


test("shows the learner turn, public stream phases, cursor, deltas, and final server answer", async ({ page }) => {
  await initializeTutor(page, "visible-stream");
  const fixture = await startStreamingFixture();
  await page.route("**/api/tutor/chat/stream", (route) => route.continue({ url: fixture.url }));

  try {
    await page.getByTestId("tutor-question").fill("How does retrieval work?");
    await page.getByTestId("tutor-submit").click();

    await expect(page.getByTestId("tutor-user-turn")).toHaveText("How does retrieval work?");
    await expect(page.getByTestId("tutor-thinking")).toContainText("准备");
    await expect(page.getByTestId("tutor-thinking")).toContainText("检索");
    await expect(page.getByTestId("tutor-thinking")).toContainText("撰写");
    await expect(page.getByTestId("tutor-streaming-cursor")).toBeVisible();
    await expect(page.getByTestId("tutor-answer")).toContainText("First answer");
    await expect(page.getByTestId("tutor-answer")).toHaveText("Server final answer");
    await expect(page.getByTestId("tutor-streaming-cursor")).toHaveCount(0);
  } finally {
    await closeServer(fixture.server);
  }
});


test("safe failure offers retry and AI configuration while reusing the request snapshot", async ({ page }) => {
  await page.route("**/api/config/skills", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ skills: [{
      id: "skill-e2e",
      name: "Evidence coach",
      description: "Use evidence",
      instructions: "Use evidence",
      enabled: true,
      default_enabled: true,
      model_profile_id: null,
    }] }),
  }));
  await initializeTutor(page, "visible-failure");
  const requests: Array<Record<string, unknown>> = [];
  let attempt = 0;
  await page.route("**/api/tutor/chat/stream", async (route) => {
    attempt += 1;
    requests.push(route.request().postDataJSON());
    if (attempt === 1) {
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sse("run.started", { run_id: "run-failed", thread_id: "thread-e2e" })
          + sse("teacher.delta", { delta: "Untrusted partial answer" })
          + sse("run.failed", {
            run_id: "run-failed",
            code: "runtime.provider_call_failed",
            message: "secret provider response",
          }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sse("run.started", { run_id: "run-retry", thread_id: "thread-e2e" })
        + sse("teacher.delta", { delta: "Retried answer" })
        + sse("run.completed", { result: { ...finalResult, final_answer: "Retried final answer" } }),
    });
  });

  await page.getByTestId("tutor-question").fill("Retry this exact question");
  await page.getByTestId("memory-declaration-toggle").check();
  await page.getByTestId("memory-preference-key").fill("explanation_style");
  await page.getByTestId("memory-preference-value").fill("examples_first");
  await page.getByTestId("tutor-submit").click();

  const failure = page.getByTestId("tutor-failure");
  await expect(failure).toBeVisible();
  await expect(failure).toContainText("runtime.provider_call_failed");
  await expect(failure).not.toContainText("secret provider response");
  await expect(failure.getByRole("link", { name: "前往 AI 配置" })).toHaveAttribute("href", "/ai-config");
  await expect(page.getByTestId("tutor-answer")).toHaveText("");
  await failure.getByRole("button", { name: "重试" }).click();
  await expect(page.getByTestId("tutor-answer")).toHaveText("Retried final answer");

  expect(requests).toHaveLength(2);
  expect(requests[1].message).toBe(requests[0].message);
  expect(requests[0].locale).toBe("zh-CN");
  expect(requests[1].locale).toBe("zh-CN");
  expect(requests[1].skill_ids).toEqual(requests[0].skill_ids);
  expect(requests[1].memory_declaration).toEqual(requests[0].memory_declaration);
});


test("deleting the active conversation clears stale tutor state before selecting the remaining thread", async ({ page }) => {
  await initializeTutor(page, "visible-delete-reset");
  const sessionSelect = page.getByLabel("讲师会话");

  await page.getByRole("button", { name: "新建会话" }).click();
  await expect(sessionSelect.locator("option")).toHaveCount(2);
  await sessionSelect.selectOption("thread-e2e");

  await page.route("**/api/tutor/conversations/thread-e2e", (route) => route.fulfill({ status: 204 }));
  await page.route("**/api/tutor/chat/stream", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sse("run.started", { run_id: "run-stale", thread_id: "thread-e2e" })
      + sse("teacher.delta", { delta: "Stale partial answer" })
      + sse("run.failed", {
        run_id: "run-stale",
        code: "runtime.provider_call_failed",
        message: "secret provider response",
      }),
  }));

  await page.getByTestId("tutor-question").fill("Question from the deleted thread");
  await page.getByTestId("tutor-submit").click();
  await expect(page.getByTestId("tutor-user-turn")).toHaveText("Question from the deleted thread");
  await expect(page.getByTestId("tutor-answer")).toHaveText("");
  await expect(page.getByTestId("tutor-failure")).toBeVisible();
  await expect(page.getByTestId("tutor-failure").getByRole("button", { name: "重试" })).toBeVisible();

  await page.getByRole("button", { name: "删除会话" }).click();

  await expect(sessionSelect).not.toHaveValue("thread-e2e");
  await expect(page.getByTestId("tutor-user-turn")).toHaveCount(0);
  await expect(page.getByTestId("tutor-answer")).toHaveText("");
  await expect(page.getByTestId("tutor-thinking")).toHaveCount(0);
  await expect(page.getByTestId("tutor-run-status")).toHaveCount(0);
  await expect(page.getByTestId("tutor-failure")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重试" })).toHaveCount(0);
});


test("cancels an active run and resumes after one-time tool approval", async ({ page }) => {
  await initializeTutor(page, "visible-cancel-approval");
  const hanging = createServer((request, response) => {
    if (handlePreflight(request, response)) return;
    response.writeHead(200, { ...corsHeaders, "Content-Type": "text/event-stream" });
    response.write(sse("run.started", { run_id: "run-cancel", thread_id: "thread-e2e" }));
  });
  await new Promise<void>((resolve) => hanging.listen(0, "127.0.0.1", resolve));
  const address = hanging.address() as AddressInfo;
  await page.route("**/api/tutor/chat/stream", (route) => route.continue({ url: `http://127.0.0.1:${address.port}/stream` }));
  await page.route("**/api/tutor/runs/run-cancel/cancel", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ run_id: "run-cancel", status: "cancelled" }),
  }));

  try {
    await page.getByTestId("tutor-question").fill("Cancel this run");
    await page.getByTestId("tutor-submit").click();
    await page.getByRole("button", { name: "取消回答" }).click();
    await expect(page.getByTestId("tutor-run-status")).toContainText("已取消");
  } finally {
    await closeServer(hanging);
  }

  await page.unroute("**/api/tutor/chat/stream");
  await page.route("**/api/tutor/chat/stream", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sse("run.started", { run_id: "run-approval", thread_id: "thread-e2e" })
      + sse("tool.approval_required", {
        approval_id: "approval-e2e",
        run_id: "run-approval",
        server: { id: "server-e2e", name: "Files" },
        tool_name: "write_note",
        arguments: { path: "notes.md" },
        status: "pending",
        result_summary: {},
      })
      + sse("run.awaiting_approval", { run_id: "run-approval", approval_id: "approval-e2e" }),
  }));
  await page.route("**/api/tutor/runs/run-approval/tool-approvals/approval-e2e/decision", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sse("tool.started", { run_id: "run-approval", approval_id: "approval-e2e" })
      + sse("tool.completed", { run_id: "run-approval", approval_id: "approval-e2e", status: "completed" })
      + sse("teacher.delta", { delta: "Resumed answer" })
      + sse("run.completed", { result: { ...finalResult, final_answer: "Approval final answer" } }),
  }));

  await page.getByTestId("tutor-question").fill("Approve this tool");
  await page.getByTestId("tutor-submit").click();
  await expect(page.getByText("write_note", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "仅此一次批准" }).click();
  await expect(page.getByTestId("tutor-answer")).toHaveText("Approval final answer");
});


test("clears partial answer when approval resume fails", async ({ page }) => {
  await initializeTutor(page, "visible-approval-failure");
  await page.route("**/api/tutor/chat/stream", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sse("run.started", { run_id: "run-approval-failed", thread_id: "thread-e2e" })
      + sse("tool.approval_required", {
        approval_id: "approval-failed-e2e",
        run_id: "run-approval-failed",
        server: { id: "server-e2e", name: "Files" },
        tool_name: "write_note",
        arguments: { path: "notes.md" },
        status: "pending",
        result_summary: {},
      })
      + sse("run.awaiting_approval", { run_id: "run-approval-failed", approval_id: "approval-failed-e2e" }),
  }));
  await page.route("**/api/tutor/runs/run-approval-failed/tool-approvals/approval-failed-e2e/decision", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sse("tool.started", { run_id: "run-approval-failed", approval_id: "approval-failed-e2e" })
      + sse("tool.completed", { run_id: "run-approval-failed", approval_id: "approval-failed-e2e", status: "completed" })
      + sse("teacher.delta", { delta: "Untrusted resumed partial answer" })
      + sse("run.failed", {
        run_id: "run-approval-failed",
        code: "runtime.provider_call_failed",
        message: "secret provider response",
      }),
  }));

  await page.getByTestId("tutor-question").fill("Fail after approval");
  await page.getByTestId("tutor-submit").click();
  await page.getByRole("button", { name: "仅此一次批准" }).click();

  const failure = page.getByTestId("tutor-failure");
  await expect(failure).toBeVisible();
  await expect(failure).toContainText("runtime.provider_call_failed");
  await expect(failure).not.toContainText("secret provider response");
  await expect(failure.getByRole("button", { name: "重试" })).toBeVisible();
  await expect(failure.getByRole("link", { name: "前往 AI 配置" })).toHaveAttribute("href", "/ai-config");
  await expect(page.getByTestId("tutor-answer")).toHaveText("");
});
