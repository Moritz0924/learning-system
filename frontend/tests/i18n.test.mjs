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

test("visible tutor, dynamic diagnostic, roadmap, and web-source copy exists in both locales", () => {
  const keys = [
    "tutor.phase.preparing",
    "tutor.phase.retrieving",
    "tutor.phase.writing",
    "tutor.phase.awaiting_approval",
    "tutor.phase.completed",
    "tutor.phase.failed",
    "tutor.phase.cancelled",
    "tutor.failureTitle",
    "tutor.failureBody",
    "tutor.taskContextMismatch",
    "tutor.errorCode",
    "tutor.retry",
    "tutor.openAiConfig",
    "tutor.runtimeUnknown",
    "onboarding.generatingQuestions",
    "onboarding.dynamicUnavailable",
    "onboarding.dynamicConfigurationInvalid",
    "onboarding.dynamicProviderUnavailable",
    "onboarding.dynamicOutputInvalid",
    "onboarding.dynamicRoadmapInfeasible",
    "onboarding.authRequired",
    "onboarding.openAiConfig",
    "config.reasoningWorkflowHelp",
    "roadmap.reassess",
    "roadmap.current",
    "roadmap.locked",
    "roadmap.completed",
    "roadmap.progress",
    "roadmap.empty",
    "shell.demoMastery",
    "shell.diagnosticEvidence",
    "source.webUnverified",
    "source.browsingOnly",
    "source.unavailable",
  ];

  for (const locale of ["zh-CN", "en-US"]) {
    for (const key of keys) assert.notEqual(translate(locale, key), key, `${locale} ${key}`);
  }
  assert.equal(translate("zh-CN", "source.webUnverified"), "外部网络来源 / 需核验");
  assert.equal(translate("zh-CN", "source.browsingOnly"), "在线推荐仅供浏览，不属于知识库证据");
  assert.equal(translate("en-US", "source.webUnverified"), "External web source / verify before use");
  assert.equal(
    translate("zh-CN", "tutor.taskContextMismatch"),
    "该任务属于旧学习计划，请返回当前学习路径。",
  );
});
