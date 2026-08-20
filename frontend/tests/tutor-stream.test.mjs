import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelTutorRequest,
  consumeTutorEventStream,
  isTutorStreamCurrent,
  reduceTutorRunView,
  startTutorRunView,
} from "../lib/tutor-stream.mjs";


test("decodes fragmented UTF-8 SSE frames in order", async () => {
  const encoder = new TextEncoder();
  const chunks = [
    "event: run.started\ndata: {\"run_id\":\"run-1\",\"thread_id\":\"thread-1\"}\n\nevent: teacher.delta\nda",
    "ta: {\"delta\":\"你",
    "好\"}\n\nevent: run.completed\ndata: {\"result\":{\"final_answer\":\"你好\",\"citations\":[]}}\n\n",
  ];
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  const received = [];

  await consumeTutorEventStream(new Response(stream), (event) => received.push(event));

  assert.deepEqual(received, [
    { type: "run.started", data: { run_id: "run-1", thread_id: "thread-1" } },
    { type: "teacher.delta", data: { delta: "你好" } },
    {
      type: "run.completed",
      data: { result: { final_answer: "你好", citations: [] } },
    },
  ]);
});


test("rejects event names outside the public tutor stream allowlist", async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(
          "event: internal.state\ndata: {\"prompt\":\"secret\"}\n\n",
        ),
      );
      controller.close();
    },
  });

  await assert.rejects(
    consumeTutorEventStream(new Response(stream), () => {}),
    /Unsupported tutor stream event/,
  );
});


test("accepts the public tool approval lifecycle events", async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode([
        'event: tool.approval_required',
        'data: {"approval_id":"approval-1","run_id":"run-1","server":{"id":"server-1","name":"Files"},"tool_name":"write","arguments":{"path":"notes.md"},"status":"pending","result_summary":{}}',
        "",
        'event: run.awaiting_approval',
        'data: {"run_id":"run-1","approval_id":"approval-1"}',
        "",
        'event: tool.started',
        'data: {"run_id":"run-1","approval_id":"approval-1"}',
        "",
        'event: tool.completed',
        'data: {"run_id":"run-1","approval_id":"approval-1","status":"completed"}',
        "",
        "",
      ].join("\n")));
      controller.close();
    },
  });
  const received = [];

  await consumeTutorEventStream(new Response(stream), (event) => received.push(event));

  assert.deepEqual(received.map((event) => event.type), [
    "tool.approval_required",
    "run.awaiting_approval",
    "tool.started",
    "tool.completed",
  ]);
});


test("parses multiple CRLF frames when delimiters split across chunks", async () => {
  const encoder = new TextEncoder();
  const chunks = [
    'event: run.started\r\ndata: {"run_id":"run-crlf","thread_id":"thread-crlf"}\r',
    '\n\r',
    '\nevent: teacher.delta\r\ndata: {"delta":"first"}\r\n\r',
    '\nevent: teacher.delta\r\ndata: {"delta":"second"}\r\n\r\n',
  ];
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  const received = [];

  await consumeTutorEventStream(new Response(stream), (event) => received.push(event));

  assert.deepEqual(received, [
    {
      type: "run.started",
      data: { run_id: "run-crlf", thread_id: "thread-crlf" },
    },
    { type: "teacher.delta", data: { delta: "first" } },
    { type: "teacher.delta", data: { delta: "second" } },
  ]);
});


test("stale stream identity cannot update a newer request or another thread", () => {
  const requestA = { threadId: "thread-a" };
  const requestB = { threadId: "thread-b" };

  assert.equal(isTutorStreamCurrent(requestA, requestA, "thread-a"), true);
  assert.equal(isTutorStreamCurrent(requestB, requestA, "thread-a"), false);
  assert.equal(isTutorStreamCurrent(requestA, requestA, "thread-b"), false);
});


test("delayed cancellation aborts its captured run and never the replacement", async () => {
  const controllerA = new AbortController();
  const controllerB = new AbortController();
  let releaseCancel;
  const cancelReleased = new Promise((resolve) => { releaseCancel = resolve; });
  const requestA = {
    runId: "run-a",
    threadId: "thread-a",
    controller: controllerA,
  };

  const cancelling = cancelTutorRequest(requestA, async (runId) => {
    assert.equal(runId, "run-a");
    await cancelReleased;
  });
  const activeRequest = {
    runId: "run-b",
    threadId: "thread-b",
    controller: controllerB,
  };
  assert.equal(activeRequest.runId, "run-b");
  assert.equal(controllerA.signal.aborted, false);
  releaseCancel();
  await cancelling;

  assert.equal(controllerA.signal.aborted, true);
  assert.equal(controllerB.signal.aborted, false);
});


test("public tutor state moves from thinking to multi-delta writing and server completion", () => {
  let view = startTutorRunView("How does retrieval work?");
  assert.deepEqual(view, {
    phase: "preparing",
    currentQuestion: "How does retrieval work?",
    errorCode: "",
    draftAnswer: "",
  });

  view = reduceTutorRunView(view, { type: "run.started", data: { run_id: "run-1" } });
  assert.equal(view.phase, "preparing");
  view = reduceTutorRunView(view, { type: "node.started", data: { node: "retrieval" } });
  assert.equal(view.phase, "retrieving");
  view = reduceTutorRunView(view, { type: "node.started", data: { node: "teacher" } });
  assert.equal(view.phase, "writing");
  view = reduceTutorRunView(view, { type: "teacher.delta", data: { delta: "First " } });
  view = reduceTutorRunView(view, { type: "teacher.delta", data: { delta: "answer" } });
  assert.equal(view.draftAnswer, "First answer");

  view = reduceTutorRunView(view, {
    type: "run.completed",
    data: { result: { final_answer: "Server final answer", citations: [] } },
  });
  assert.equal(view.phase, "completed");
  assert.equal(view.draftAnswer, "Server final answer");
});


test("public tutor state keeps only a sanitized failure code", () => {
  const partial = reduceTutorRunView(
    startTutorRunView("Fail safely"),
    { type: "teacher.delta", data: { delta: "Partial" } },
  );

  const failed = reduceTutorRunView(partial, {
    type: "run.failed",
    data: { code: "../../secret provider body", message: "raw secret" },
  });

  assert.equal(failed.phase, "failed");
  assert.equal(failed.errorCode, "tutor.run_failed");
  assert.equal(failed.draftAnswer, "Partial");
  assert.equal(JSON.stringify(failed).includes("raw secret"), false);
});


test("approval resume and cancellation stay in the public lifecycle", () => {
  let view = reduceTutorRunView(startTutorRunView("Use a tool"), {
    type: "tool.approval_required",
    data: { approval_id: "approval-1", run_id: "run-1" },
  });
  assert.equal(view.phase, "awaiting_approval");

  view = reduceTutorRunView(view, {
    type: "tool.started",
    data: { approval_id: "approval-1", run_id: "run-1" },
  });
  assert.equal(view.phase, "preparing");
  view = reduceTutorRunView(view, { type: "teacher.delta", data: { delta: "Resumed" } });
  assert.equal(view.phase, "writing");

  view = reduceTutorRunView(view, { type: "run.cancelled", data: { run_id: "run-1" } });
  assert.equal(view.phase, "cancelled");
});
