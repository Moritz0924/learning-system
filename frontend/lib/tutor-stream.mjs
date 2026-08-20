const TUTOR_EVENT_TYPES = new Set([
  "run.started",
  "node.started",
  "retrieval.completed",
  "teacher.delta",
  "node.completed",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "tool.approval_required",
  "run.awaiting_approval",
  "tool.started",
  "tool.completed",
]);


export function isTutorStreamCurrent(activeRequest, request, activeThreadId) {
  return activeRequest === request && request?.threadId === activeThreadId;
}


export function startTutorRunView(question) {
  return {
    phase: "preparing",
    currentQuestion: question,
    errorCode: "",
    draftAnswer: "",
  };
}


export function reduceTutorRunView(view, event) {
  if (event.type === "run.started") return { ...view, phase: "preparing", errorCode: "" };
  if (event.type === "node.started" && event.data.node === "retrieval") {
    return { ...view, phase: "retrieving" };
  }
  if (event.type === "node.started" && event.data.node === "teacher") {
    return { ...view, phase: "writing" };
  }
  if (event.type === "teacher.delta") {
    const delta = typeof event.data.delta === "string" ? event.data.delta : "";
    return { ...view, phase: "writing", draftAnswer: view.draftAnswer + delta };
  }
  if (event.type === "run.completed") {
    const result = event.data.result;
    const finalAnswer = result && typeof result === "object" && !Array.isArray(result)
      && typeof result.final_answer === "string"
      ? result.final_answer
      : view.draftAnswer;
    return { ...view, phase: "completed", errorCode: "", draftAnswer: finalAnswer };
  }
  if (event.type === "run.failed") {
    const code = typeof event.data.code === "string" && /^(tutor|runtime|mcp)\.[a-z0-9_.-]{1,88}$/.test(event.data.code)
      ? event.data.code
      : "tutor.run_failed";
    return { ...view, phase: "failed", errorCode: code };
  }
  if (event.type === "run.cancelled") return { ...view, phase: "cancelled", errorCode: "" };
  if (event.type === "tool.approval_required" || event.type === "run.awaiting_approval") {
    return { ...view, phase: "awaiting_approval" };
  }
  if (event.type === "tool.started") return { ...view, phase: "preparing" };
  return view;
}


export async function cancelTutorRequest(request, cancelRequest) {
  if (!request?.runId) return;
  const { runId, controller } = request;
  try {
    await cancelRequest(runId);
  } finally {
    controller.abort();
  }
}


export async function consumeTutorEventStream(response, onEvent) {
  if (!response.body) throw new Error("Tutor stream response has no body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    buffer = buffer.replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (frame.trim()) onEvent(parseTutorEvent(frame));
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) onEvent(parseTutorEvent(buffer));
}


function parseTutorEvent(frame) {
  let type = "";
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!TUTOR_EVENT_TYPES.has(type)) {
    throw new Error(`Unsupported tutor stream event: ${type || "missing"}`);
  }
  if (dataLines.length === 0) throw new Error("Tutor stream event has no data");
  const data = JSON.parse(dataLines.join("\n"));
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("Tutor stream event data must be an object");
  }
  return { type, data };
}
