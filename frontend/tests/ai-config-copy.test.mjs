import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { translate, translateModelTestFailure } from "../lib/i18n.mjs";


const nextConfigSource = new URL("../next.config.mjs", import.meta.url);


test("AI 配置中心为两种语言提供完整的可见文案", () => {
  assert.equal(translate("zh-CN", "config.title"), "AI 配置");
  assert.equal(translate("zh-CN", "config.modelProfiles"), "模型配置");
  assert.equal(translate("en-US", "config.title"), "AI configuration");
  assert.equal(translate("en-US", "config.modelProfiles"), "Model profiles");
});


test("开发模式不显示 Next.js 英文浮层", async () => {
  const source = await readFile(nextConfigSource, "utf8");

  assert.match(source, /devIndicators:\s*false/);
});


test("模型测试会按当前语言翻译安全的上游认证错误", () => {
  assert.equal(translateModelTestFailure("zh-CN", "model_test.provider_http_401"), "API 密钥无效、已失效或没有访问权限。");
  assert.equal(translateModelTestFailure("en-US", "model_test.provider_http_401"), "The API secret is invalid, expired, or lacks access.");
});
