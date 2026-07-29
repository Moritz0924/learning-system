import assert from "node:assert/strict";
import test from "node:test";

import { consumeTutorEventStream } from "../lib/tutor-stream.mjs";


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
