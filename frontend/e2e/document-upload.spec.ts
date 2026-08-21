import { expect, test, type Page, type Route } from "@playwright/test";

import { fillDiagnosis, registerForDiagnosis } from "./onboarding-helpers";


type DocumentStatus = "pending" | "processing" | "success" | "failed";

function documentPayload(
  id: string,
  filename: string,
  parseStatus: DocumentStatus,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    filename,
    mime_type: filename.endsWith(".md") ? "text/markdown" : "application/pdf",
    size_bytes: 24,
    parse_status: parseStatus,
    parse_error_code: null,
    parse_error: null,
    page_count: parseStatus === "success" ? 1 : null,
    block_count: parseStatus === "success" ? 2 : null,
    parser_version: parseStatus === "success" ? "document-parser-v2" : null,
    created_at: "2026-07-18T08:00:00Z",
    processing_started_at: parseStatus === "pending" ? null : "2026-07-18T08:00:01Z",
    processing_completed_at: ["success", "failed"].includes(parseStatus)
      ? "2026-07-18T08:00:02Z"
      : null,
    ...overrides,
  };
}

async function initializeAndOpenSettings(page: Page, prefix: string) {
  await registerForDiagnosis(page, prefix);
  await fillDiagnosis(page);
  await page.getByTestId("create-learning-path").click();
  await expect(page).toHaveURL(/\/path$/);
  await page.goto("/settings");
  await expect(page.getByTestId("document-file-input")).toBeAttached();
}

function isDocumentsRoute(route: Route) {
  return new URL(route.request().url()).pathname.startsWith("/api/documents");
}

test("selects a file, sends browser-generated multipart, and polls to success", async ({ page }) => {
  const pending = documentPayload("doc-success", "lesson.md", "pending");
  const complete = documentPayload("doc-success", "lesson.md", "success");
  await page.route("**/api/documents**", async (route) => {
    if (!isDocumentsRoute(route)) return route.continue();
    const method = route.request().method();
    const path = new URL(route.request().url()).pathname;
    if (method === "POST" && path === "/api/documents") {
      return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(pending) });
    }
    if (method === "GET" && path === "/api/documents/doc-success") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(complete) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents: [] }) });
  });
  await initializeAndOpenSettings(page, "upload-success");

  await page.getByTestId("document-file-input").setInputFiles({
    name: "lesson.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Retrieval notes"),
  });
  await expect(page.getByTestId("selected-document-name")).toHaveText("lesson.md");
  const uploadRequestPromise = page.waitForRequest((request) =>
    request.method() === "POST" && new URL(request.url()).pathname === "/api/documents"
  );
  await page.getByTestId("upload-selected-document").click();
  const uploadRequest = await uploadRequestPromise;
  const multipartContentType = uploadRequest.headers()["content-type"] || "";
  const multipartBody = uploadRequest.postDataBuffer()?.toString("utf8") || "";

  expect(multipartContentType).toContain("multipart/form-data; boundary=");
  expect(multipartContentType).not.toBe("multipart/form-data");
  expect(multipartBody).toContain('name="file"');
  expect(multipartBody).toContain('filename="lesson.md"');
  await expect(page.getByTestId("document-status-success")).toBeVisible();
  await expect(page.getByText("1 页 · 2 块")).toBeVisible();
});


test("polls a failed document and exposes only the safe processing error", async ({ page }) => {
  const pending = documentPayload("doc-failed", "broken.pdf", "pending");
  const failed = documentPayload("doc-failed", "broken.pdf", "failed", {
    parse_error_code: "document.invalid_pdf",
    parse_error: "The PDF could not be parsed.",
  });
  await page.route("**/api/documents**", async (route) => {
    if (!isDocumentsRoute(route)) return route.continue();
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(pending) });
    }
    if (path === "/api/documents/doc-failed") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(failed) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents: [] }) });
  });
  await initializeAndOpenSettings(page, "upload-failed");
  await page.getByTestId("document-file-input").setInputFiles({
    name: "broken.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%%EOF"),
  });
  await page.getByTestId("upload-selected-document").click();

  const failedRow = page.getByTestId("document-row").filter({ hasText: "broken.pdf" });
  await expect(failedRow.getByTestId("document-status-failed")).toBeVisible();
  await expect(failedRow.getByText("The PDF could not be parsed.")).toBeVisible();
  await expect(failedRow.getByText("document.invalid_pdf")).toBeVisible();
  await expect(page.getByText(/object_key|provider|traceback/i)).toHaveCount(0);
});


test("keeps a newly selected file when an earlier upload completes", async ({ page }) => {
  let releaseUpload: (() => void) | undefined;
  const uploadReleased = new Promise<void>((resolve) => { releaseUpload = resolve; });
  await page.route("**/api/documents**", async (route) => {
    if (!isDocumentsRoute(route)) return route.continue();
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST" && path === "/api/documents") {
      await uploadReleased;
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(documentPayload("doc-race", "first.md", "pending")),
      });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents: [] }) });
  });
  await initializeAndOpenSettings(page, "upload-reselect");
  const input = page.getByTestId("document-file-input");
  await input.setInputFiles({ name: "first.md", mimeType: "text/markdown", buffer: Buffer.from("first") });
  await page.getByTestId("upload-selected-document").click();
  await input.setInputFiles({ name: "second.md", mimeType: "text/markdown", buffer: Buffer.from("second") });
  releaseUpload?.();

  await expect(page.getByTestId("upload-selected-document")).toBeEnabled();
  await expect(page.getByTestId("selected-document-name")).toHaveText("second.md");
});


test("cancels old-user polling and does not show its response to a new identity", async ({ page }) => {
  let releasePoll: (() => void) | undefined;
  const pollReleased = new Promise<void>((resolve) => { releasePoll = resolve; });
  await page.route("**/api/documents**", async (route) => {
    if (!isDocumentsRoute(route)) return route.continue();
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(documentPayload("old-user-doc", "old-user.md", "pending")),
      });
    }
    if (path === "/api/documents/old-user-doc") {
      await pollReleased;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(documentPayload("old-user-doc", "old-user.md", "success")),
      });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents: [] }) });
  });
  await initializeAndOpenSettings(page, "upload-identity-a");
  await page.getByTestId("document-file-input").setInputFiles({
    name: "old-user.md", mimeType: "text/markdown", buffer: Buffer.from("old user"),
  });
  await page.getByTestId("upload-selected-document").click();
  await expect(page.getByTestId("document-row").filter({ hasText: "old-user.md" })).toBeVisible();
  await page.waitForTimeout(1_100);
  await page.getByTitle("账户").click();
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/login/);
  releasePoll?.();
  await registerForDiagnosis(page, "upload-identity-b");
  await page.goto("/settings");

  await expect(page.getByTestId("document-file-input")).toBeAttached();
  await expect(page.getByTestId("document-row").filter({ hasText: "old-user.md" })).toHaveCount(0);
});


test("rejects invalid files locally without making an upload request", async ({ page }) => {
  let postCount = 0;
  await page.route("**/api/documents**", async (route) => {
    if (!isDocumentsRoute(route)) return route.continue();
    if (route.request().method() === "POST") postCount += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents: [] }) });
  });
  await initializeAndOpenSettings(page, "upload-invalid");
  await page.getByTestId("document-file-input").setInputFiles({
    name: "malware.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ"),
  });

  await expect(page.getByTestId("document-validation-error")).toBeVisible();
  await expect(page.getByTestId("upload-selected-document")).toBeDisabled();
  expect(postCount).toBe(0);
});


test("keeps the selected file after a safe server rejection and prevents duplicate submit", async ({ page }) => {
  let postCount = 0;
  await page.route("**/api/documents**", async (route) => {
    if (!isDocumentsRoute(route)) return route.continue();
    if (route.request().method() === "POST") {
      postCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 200));
      return route.fulfill({
        status: 415,
        contentType: "application/json",
        body: JSON.stringify({ detail: { code: "document.unsupported_media_type", message: "This file type is not supported." } }),
      });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ documents: [] }) });
  });
  await initializeAndOpenSettings(page, "upload-rejected");
  await page.getByTestId("document-file-input").setInputFiles({
    name: "rejected.md", mimeType: "text/markdown", buffer: Buffer.from("safe markdown"),
  });
  await page.getByTestId("upload-selected-document").dblclick();

  await expect(page.locator("div.fixed").filter({ hasText: "不支持此文件类型。" })).toBeVisible();
  await expect(page.getByTestId("selected-document-name")).toHaveText("rejected.md");
  expect(postCount).toBe(1);
});
