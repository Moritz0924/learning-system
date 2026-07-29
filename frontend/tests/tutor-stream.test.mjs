import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelTutorRequest,
  consumeTutorEventStream,
  isTutorStreamCurrent,
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
