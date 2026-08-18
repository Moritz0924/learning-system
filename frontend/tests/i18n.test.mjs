import assert from "node:assert/strict";
import test from "node:test";

import { translate, translateStatus } from "../lib/i18n.mjs";

test("locale catalog renders the same product control in Chinese and English", () => {
  assert.equal(translate("zh-CN", "common.saveChanges"), "保存更改");
  assert.equal(translate("en-US", "common.saveChanges"), "Save changes");
});

test("locale catalog interpolates controlled runtime status without translating user content", () => {
  assert.equal(translate("en-US", "shell.filesCount", { count: 3 }), "3 files");
  assert.equal(translate("zh-CN", "shell.filesCount", { count: 3 }), "3 个文件");
  assert.equal(translate("en-US", "unknown.user-provided-name"), "unknown.user-provided-name");
});

test("locale catalog maps known lifecycle values and leaves unknown server values intact", () => {
  assert.equal(translateStatus("en-US", "completed"), "Completed");
  assert.equal(translateStatus("zh-CN", "completed"), "已完成");
  assert.equal(translateStatus("en-US", "custom-provider-state"), "custom-provider-state");
});
