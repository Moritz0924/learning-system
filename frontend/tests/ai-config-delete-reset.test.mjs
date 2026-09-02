import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("deleting each AI configuration resets its full editor through choose", async () => {
  const source = await readFile(new URL("../features/ai-config/ai-config-console.tsx", import.meta.url), "utf8");
  const deleteSuccessHandlers = source.match(/remove(?:Model|Skill|McpServer)\([^)]*\)\.then\(\(\) => \{[^}]*\}/g) ?? [];

  assert.equal(deleteSuccessHandlers.length, 3);
  for (const handler of deleteSuccessHandlers) {
    assert.match(handler, /choose\(\)/);
    assert.doesNotMatch(handler, /setSelectedId/);
  }
});
