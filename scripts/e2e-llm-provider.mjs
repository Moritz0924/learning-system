import { createServer } from "node:http";


const port = Number(process.env.E2E_LLM_PORT || 8124);

const diagnostic = JSON.stringify({
  title: "Core loop readiness check",
  questions: [1, 2, 3].map((number) => ({
    question_id: `question-${number}`,
    skill_id: `skill-${number}`,
    prompt: `Core loop question ${number}?`,
    options: [
      { option_id: "a", label: `Answer A${number}` },
      { option_id: "b", label: `Answer B${number}` },
    ],
    correct_option_id: "a",
  })),
});

const roadmap = JSON.stringify({
  title: "Core learning loop roadmap",
  stages: [
    {
      stage_id: "stage-a",
      title: "Foundation",
      objective: "Build the foundation.",
      order: 1,
      nodes: [{ node_id: "node-a", skill_id: "skill-1", title: "Node A", objective: "Learn Node A.", order: 1, estimated_minutes: 30, due_day: 1 }],
    },
    {
      stage_id: "stage-b",
      title: "Practice",
      objective: "Practice the learning workflow.",
      order: 2,
      nodes: [{ node_id: "node-b", skill_id: "skill-2", title: "Node B", objective: "Learn Node B.", order: 1, estimated_minutes: 30, due_day: 1 }],
    },
    {
      stage_id: "stage-c",
      title: "Delivery",
      objective: "Complete the learning workflow.",
      order: 3,
      nodes: [{ node_id: "node-c", skill_id: "skill-3", title: "Node C", objective: "Learn Node C.", order: 1, estimated_minutes: 30, due_day: 1 }],
    },
  ],
});

function completionFor(body) {
  const prompt = Array.isArray(body.messages)
    ? body.messages.map((message) => String(message?.content ?? "")).join("\n")
    : "";
  if (prompt.includes("Create 3-5 single-choice diagnostic questions")) return diagnostic;
  if (prompt.includes("Create 3-8 ordered learning stages")) return roadmap;
  return "节点 B 的可靠回答";
}

const server = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end('{"status":"ok"}');
    return;
  }
  if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
    response.writeHead(404);
    response.end();
    return;
  }
  const chunks = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const content = completionFor(body);
    if (body.stream) {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.write(`data: ${JSON.stringify({ choices: [{ delta: { content } }] })}\n\n`);
      response.end("data: [DONE]\n\n");
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({
      choices: [{ finish_reason: "stop", message: { content } }],
      usage: { prompt_tokens: 1, completion_tokens: 1 },
    }));
  });
});

server.listen(port, "127.0.0.1");
