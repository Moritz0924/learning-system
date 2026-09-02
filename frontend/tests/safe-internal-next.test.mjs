import assert from "node:assert/strict";
import test from "node:test";

import { safeInternalNext } from "../lib/safe-internal-next.mjs";


test("safeInternalNext preserves an internal path with task and node query", () => {
  assert.equal(
    safeInternalNext("/tutor?task=task-1&node=node-1"),
    "/tutor?task=task-1&node=node-1",
  );
  assert.equal(
    safeInternalNext("/tutor?task=a%2Fb&q=x%23y&progress=100%25&name=%E5%AD%A6%E4%B9%A0"),
    "/tutor?task=a%2Fb&q=x%23y&progress=100%25&name=%E5%AD%A6%E4%B9%A0",
  );
});


test("safeInternalNext rejects redirects outside the current site", () => {
  for (const value of [
    "https://evil.example/path",
    "//evil.example/path",
    "/\\evil.example/path",
    "\\evil.example\\path",
    "javascript:alert(1)",
    "%E0%A4%A",
    "/path?value=\uFFFD",
  ]) {
    assert.equal(safeInternalNext(value), "/diagnosis", value);
  }
});
