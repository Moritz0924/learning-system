import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const consoleSource = new URL("../features/ai-config/ai-config-console.tsx", import.meta.url);
const nextConfigSource = new URL("../next.config.mjs", import.meta.url);


test("AI 配置中心使用中文可见文案", async () => {
  const source = await readFile(consoleSource, "utf8");

  for (const text of ["AI 配置", "模型配置", "技能", "服务器与工具", "保存更改"]) {
    assert.match(source, new RegExp(text));
  }
  assert.doesNotMatch(source, /AI configuration|Model profiles|Save changes/);
});


test("开发模式不显示 Next.js 英文浮层", async () => {
  const source = await readFile(nextConfigSource, "utf8");

  assert.match(source, /devIndicators:\s*false/);
});


test("模型测试会将安全的上游认证错误翻译为中文提示", async () => {
  const source = await readFile(consoleSource, "utf8");

  assert.match(source, /model_test\.provider_http_401/);
  assert.match(source, /API 密钥无效、已失效或没有访问权限/);
});
