import assert from "node:assert/strict";
import test from "node:test";

import { shouldNavigateToAiConfig } from "../lib/ai-config-shortcut.mjs";


test("Ctrl/Cmd+, opens AI config outside editable controls", () => {
  for (const modifier of [{ ctrlKey: true }, { metaKey: true }]) {
    assert.equal(shouldNavigateToAiConfig({ key: ",", target: { tagName: "DIV" }, ...modifier }), true);
  }
  assert.equal(shouldNavigateToAiConfig({ key: ".", ctrlKey: true, target: { tagName: "DIV" } }), false);
  assert.equal(shouldNavigateToAiConfig({ key: ",", target: { tagName: "DIV" } }), false);
});


test("AI config shortcut leaves editable targets alone", () => {
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    assert.equal(shouldNavigateToAiConfig({ key: ",", ctrlKey: true, target: { tagName } }), false);
  }
  assert.equal(
    shouldNavigateToAiConfig({ key: ",", metaKey: true, target: { tagName: "DIV", isContentEditable: true } }),
    false,
  );
  assert.equal(
    shouldNavigateToAiConfig({
      key: ",",
      ctrlKey: true,
      target: { tagName: "SPAN", closest: (selector) => selector === '[contenteditable="true"]' ? {} : null },
    }),
    false,
  );
});
