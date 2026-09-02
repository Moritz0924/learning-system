import assert from "node:assert/strict";
import test from "node:test";

import { safeInternalNext } from "../lib/safe-internal-next.mjs";


test("safeInternalNext preserves an internal path with task and node query", () => {
  assert.equal(
    safeInternalNext("/tutor?task=task-1&node=node-1"),
    "/tutor?task=task-1&node=node-1",
  );
  assert.equal(safeInternalNext("%2Fpath%3Fnode%3Dnode-2"), "/path?node=node-2");
});


test("safeInternalNext rejects redirects outside the current site", () => {
  for (const value of [
    "https://evil.example/path",
    "//evil.example/path",
    "/\\evil.example/path",
    "\\evil.example\\path",
    "javascript:alert(1)",
    "%E0%A4%A",
  ]) {
    assert.equal(safeInternalNext(value), "/diagnosis", value);
  }
});
