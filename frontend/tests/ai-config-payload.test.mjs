import assert from "node:assert/strict";
import test from "node:test";

import { mcpWritePayload, modelWritePayload, skillWritePayload } from "../lib/ai-config-payload.mjs";


test("model and skill edit payloads omit response-only fields", () => {
  assert.deepEqual(modelWritePayload({ id: "model-1", name: "Chat", capability: "chat", provider: "openai_compatible", base_url: "https://api.example/v1", model_name: "chat-1", dimensions: null, enabled: true, last_test_status: "success" }), {
    name: "Chat", capability: "chat", provider: "openai_compatible", base_url: "https://api.example/v1", model_name: "chat-1", dimensions: null, enabled: true,
  });
  assert.deepEqual(skillWritePayload({ id: "skill-1", name: "Explain", description: "", instructions: "Use examples", enabled: true, default_enabled: false, model_profile_id: null }), {
    name: "Explain", description: "", instructions: "Use examples", enabled: true, default_enabled: false, model_profile_id: null,
  });
});


test("MCP edit payload omits trust, test, and discovered-tool state", () => {
  assert.deepEqual(mcpWritePayload({ id: "server-1", name: "Files", transport: "stdio", url: null, command: "node", args: ["server.js"], working_directory: null, env: {}, enabled: true, trust_fingerprint: "abc", trusted_at: "2026-01-01", last_test_status: "success", tools: [{ name: "read" }] }), {
    name: "Files", transport: "stdio", url: null, command: "node", args: ["server.js"], working_directory: null, env: {}, enabled: true,
  });
});
