"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { MdArrowBack, MdBuild, MdExtension, MdHub, MdSmartToy } from "react-icons/md";

import {
  bindModel,
  createMcpServer,
  createModel,
  createSkill,
  discoverMcpServer,
  listBindings,
  listMcpServers,
  listModels,
  listSkills,
  removeMcpSecret,
  removeMcpServer,
  removeModel,
  removeModelSecret,
  removeSkill,
  saveMcpSecret,
  saveModelSecret,
  setMcpToolEnabled,
  testMcpServer,
  testModel,
  trustMcpServer,
  unbindModel,
  updateMcpServer,
  updateModel,
  updateSkill,
} from "./ai-config-api";
import type {
  AiCapability,
  CapabilityBinding,
  McpServer,
  McpServerWrite,
  ModelProfile,
  ModelProfileWrite,
  PromptSkill,
  PromptSkillWrite,
} from "./types";
import { mcpWritePayload, modelWritePayload, skillWritePayload } from "@/lib/ai-config-payload.mjs";

type Category = "models" | "skills" | "mcp";

const categories = [
  { id: "models" as const, label: "模型", detail: "运行时配置", icon: MdSmartToy },
  { id: "skills" as const, label: "技能", detail: "导师指令", icon: MdExtension },
  { id: "mcp" as const, label: "MCP", detail: "服务器与工具", icon: MdHub },
];

const capabilityLabels: Record<AiCapability, string> = {
  chat: "对话",
  reasoning: "推理",
  vision: "视觉",
  embedding: "向量嵌入",
};

const statusLabels: Record<string, string> = {
  enabled: "已启用",
  disabled: "已停用",
  default: "默认",
  success: "成功",
  failed: "失败",
  unknown: "未知",
};

const statusLabel = (status: string) => statusLabels[status] ?? status;

const emptyModel: ModelProfileWrite = {
  name: "",
  capability: "chat",
  provider: "openai_compatible",
  base_url: "",
  model_name: "",
  dimensions: null,
  enabled: true,
};

const emptySkill: PromptSkillWrite = {
  name: "",
  description: "",
  instructions: "",
  enabled: true,
  default_enabled: false,
  model_profile_id: null,
};

const emptyMcp: McpServerWrite = {
  name: "",
  transport: "streamable_http",
  url: "",
  command: null,
  args: [],
  working_directory: null,
  env: {},
  enabled: true,
};

const loadConfiguration = () => Promise.all([listModels(), listBindings(), listSkills(), listMcpServers()]);

export function AiConfigConsole() {
  const [category, setCategory] = useState<Category>("models");
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [bindings, setBindings] = useState<CapabilityBinding[]>([]);
  const [skills, setSkills] = useState<PromptSkill[]>([]);
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [modelResult, bindingResult, skillResult, serverResult] = await loadConfiguration();
      setModels(modelResult.models);
      setBindings(bindingResult.bindings);
      setSkills(skillResult.skills);
      setServers(serverResult.mcp_servers);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加载配置。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void loadConfiguration()
      .then(([modelResult, bindingResult, skillResult, serverResult]) => {
        if (cancelled) return;
        setModels(modelResult.models);
        setBindings(bindingResult.bindings);
        setSkills(skillResult.skills);
        setServers(serverResult.mcp_servers);
      })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : "无法加载配置。"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return (
    <main className="min-h-screen bg-[#f4f7f7] text-ink">
      <header className="flex h-16 items-center justify-between border-b border-line bg-white px-5">
        <div className="flex items-center gap-3">
          <Link className="grid h-9 w-9 place-items-center rounded-lg border border-line text-muted hover:bg-[#f1f6f6]" href="/today" aria-label="返回学习页面">
            <MdArrowBack />
          </Link>
          <div>
            <div className="text-sm font-semibold">AI 配置</div>
            <div className="text-xs text-muted">模型、技能与工具连接</div>
          </div>
        </div>
        <div className="rounded-lg border border-line bg-[#fbfdfc] px-3 py-2 text-xs text-muted">Ctrl/Cmd + ,</div>
      </header>
      {error && <div role="alert" className="border-b border-red-200 bg-red-50 px-5 py-3 text-sm text-red-700">{error}</div>}
      {loading ? (
        <div className="grid min-h-[calc(100vh-64px)] place-items-center text-sm text-muted">正在加载配置…</div>
      ) : (
        <div className="grid min-h-[calc(100vh-64px)] grid-cols-[180px_300px_minmax(520px,1fr)] max-[900px]:grid-cols-[150px_240px_minmax(420px,1fr)] max-[720px]:block">
          <aside className="border-r border-line bg-[#f8fbfb] p-3">
            <div className="px-3 pb-3 pt-2 text-[11px] font-semibold tracking-[0.16em] text-muted">配置</div>
            {categories.map((item) => {
              const Icon = item.icon;
              return (
                <button key={item.id} type="button" onClick={() => setCategory(item.id)} className={`mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left ${category === item.id ? "bg-white text-teal shadow-sm" : "text-muted hover:bg-white/80"}`}>
                  <Icon size={20} />
                  <span><span className="block text-sm font-semibold">{item.label}</span><span className="block text-[11px]">{item.detail}</span></span>
                </button>
              );
            })}
          </aside>
          {category === "models" && <ModelPanel models={models} bindings={bindings} refresh={refresh} />}
          {category === "skills" && <SkillPanel skills={skills} models={models} refresh={refresh} />}
          {category === "mcp" && <McpPanel servers={servers} refresh={refresh} />}
        </div>
      )}
    </main>
  );
}

function ModelPanel({ models, bindings, refresh }: { models: ModelProfile[]; bindings: CapabilityBinding[]; refresh: () => Promise<void> }) {
  const [selectedId, setSelectedId] = useState(models[0]?.id ?? "new");
  const selected = models.find((item) => item.id === selectedId);
  const [draft, setDraft] = useState<ModelProfileWrite>(selected ? modelWritePayload(selected) : emptyModel);
  const [secret, setSecret] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);

  const choose = (model?: ModelProfile) => {
    setSelectedId(model?.id ?? "new");
    setDraft(model ? modelWritePayload(model) : emptyModel);
    setSecret("");
    setResult("");
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setResult("");
    try {
      const payload = { ...draft, dimensions: draft.capability === "embedding" ? 1536 : null };
      const saved = selected ? await updateModel(selected.id, payload) : await createModel(payload);
      if (secret) {
        const status = await saveModelSecret(saved.id, secret);
        setResult(`已保存，密钥 ${status.masked_value}。`);
      } else setResult("已保存。");
      setSelectedId(saved.id); setSecret(""); await refresh();
    } catch (cause) { setResult(cause instanceof Error ? cause.message : "保存失败。"); }
    finally { setBusy(false); }
  };

  const binding = bindings.find((item) => item.capability === draft.capability);
  const isBound = Boolean(selected && binding?.model_profile_id === selected.id);

  return (
    <>
      <ListPane title="模型配置" onNew={() => choose()}>
        {models.map((model) => <ItemButton key={model.id} selected={selectedId === model.id} title={model.name} subtitle={`${capabilityLabels[model.capability]} · ${model.model_name}`} onClick={() => choose(model)} status={model.enabled ? model.last_test_status || "enabled" : "disabled"} />)}
      </ListPane>
      <DetailPane title={selected ? selected.name : "新建模型配置"} subtitle="兼容 OpenAI 的运行时端点">
        <form onSubmit={(event) => void save(event)} className="space-y-5">
          <div className="grid grid-cols-2 gap-4">
            <Field label="配置名称"><input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className="config-input" /></Field>
            <Field label="能力"><select value={draft.capability} onChange={(e) => setDraft({ ...draft, capability: e.target.value as AiCapability, dimensions: e.target.value === "embedding" ? 1536 : null })} className="config-input"><option value="chat">对话</option><option value="reasoning">推理</option><option value="vision">视觉</option><option value="embedding">向量嵌入</option></select></Field>
          </div>
          <Field label="基础 URL"><input required type="url" value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} placeholder="https://provider.example/v1" className="config-input" /></Field>
          <Field label="模型名称"><input required value={draft.model_name} onChange={(e) => setDraft({ ...draft, model_name: e.target.value })} className="config-input" /></Field>
          {draft.capability === "embedding" && <Field label="向量维度"><input value="1536" readOnly className="config-input bg-slate-50 text-muted" /></Field>}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> 已启用</label>
          <div className="rounded-lg border border-line bg-[#fbfdfc] p-4">
            <div className="text-sm font-semibold">API 密钥</div>
            <p className="mt-1 text-xs text-muted">密钥存储在数据库之外；留空会保留当前值。</p>
            <input type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} className="config-input mt-3" />
            {selected && <button type="button" className="mt-3 text-xs font-semibold text-coral" onClick={() => void removeModelSecret(selected.id).then(() => setResult("已删除已保存的密钥。")).catch((cause) => setResult(cause instanceof Error ? cause.message : "删除密钥失败。"))}>删除已保存的密钥</button>}
          </div>
          {selected && <div className="flex flex-wrap gap-2"><Action onClick={() => void testModel(selected.id).then((value) => setResult(value.status === "success" ? "连接成功。" : `连接失败${value.code ? `：${value.code}` : "。"}`)).catch((cause) => setResult(cause instanceof Error ? cause.message : "测试失败。"))}>测试连接</Action><Action onClick={() => void (isBound ? unbindModel(draft.capability) : bindModel(draft.capability, selected.id)).then(refresh).catch((cause) => setResult(cause instanceof Error ? cause.message : "绑定失败。"))}>{isBound ? `解除 ${capabilityLabels[draft.capability]} 绑定` : `用于${capabilityLabels[draft.capability]}`}</Action></div>}
          {binding && !isBound && <p className="text-xs text-muted">该能力当前已绑定到其他配置。</p>}
          <Footer busy={busy} result={result} onDelete={selected ? () => { if (window.confirm(`删除 ${selected.name}？`)) void removeModel(selected.id).then(() => { setSelectedId("new"); return refresh(); }).catch((cause) => setResult(cause instanceof Error ? cause.message : "删除失败。")); } : undefined} />
        </form>
      </DetailPane>
    </>
  );
}

function SkillPanel({ skills, models, refresh }: { skills: PromptSkill[]; models: ModelProfile[]; refresh: () => Promise<void> }) {
  const [selectedId, setSelectedId] = useState(skills[0]?.id ?? "new");
  const selected = skills.find((item) => item.id === selectedId);
  const [draft, setDraft] = useState<PromptSkillWrite>(selected ? skillWritePayload(selected) : emptySkill);
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const choose = (skill?: PromptSkill) => {
    setSelectedId(skill?.id ?? "new");
    setDraft(skill ? skillWritePayload(skill) : emptySkill);
    setResult("");
  };

  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setResult("");
    try { const saved = selected ? await updateSkill(selected.id, draft) : await createSkill(draft); setSelectedId(saved.id); setResult("已保存。"); await refresh(); }
    catch (cause) { setResult(cause instanceof Error ? cause.message : "保存失败。"); }
    finally { setBusy(false); }
  };

  return <>
    <ListPane title="导师技能" onNew={() => choose()}>{skills.map((skill) => <ItemButton key={skill.id} selected={selectedId === skill.id} title={skill.name} subtitle={skill.description || "暂无说明"} onClick={() => choose(skill)} status={!skill.enabled ? "disabled" : skill.default_enabled ? "default" : "enabled"} />)}</ListPane>
    <DetailPane title={selected ? selected.name : "新建导师技能"} subtitle="可在每次导师请求中选择的复用指令">
      <form onSubmit={(event) => void save(event)} className="space-y-5">
        <Field label="名称"><input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className="config-input" /></Field>
        <Field label="说明"><input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className="config-input" /></Field>
        <Field label="指令"><textarea required maxLength={4000} value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} className="config-input min-h-48 resize-y" /></Field>
        <Field label="模型覆盖"><select value={draft.model_profile_id ?? ""} onChange={(e) => setDraft({ ...draft, model_profile_id: e.target.value || null })} className="config-input"><option value="">使用能力绑定</option>{models.filter((model) => model.enabled && ["chat", "reasoning"].includes(model.capability)).map((model) => <option key={model.id} value={model.id}>{model.name} · {capabilityLabels[model.capability]}</option>)}</select></Field>
        <div className="flex flex-wrap gap-5 text-sm"><label className="flex items-center gap-2"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> 已启用</label><label className="flex items-center gap-2"><input type="checkbox" checked={draft.default_enabled} onChange={(e) => setDraft({ ...draft, default_enabled: e.target.checked })} /> 默认选中</label></div>
        <Footer busy={busy} result={result} onDelete={selected ? () => { if (window.confirm(`删除 ${selected.name}？`)) void removeSkill(selected.id).then(() => { setSelectedId("new"); return refresh(); }).catch((cause) => setResult(cause instanceof Error ? cause.message : "删除失败。")); } : undefined} />
      </form>
    </DetailPane>
  </>;
}

function McpPanel({ servers, refresh }: { servers: McpServer[]; refresh: () => Promise<void> }) {
  const [selectedId, setSelectedId] = useState(servers[0]?.id ?? "new");
  const selected = servers.find((item) => item.id === selectedId);
  const [draft, setDraft] = useState<McpServerWrite>(selected ? mcpWritePayload(selected) : emptyMcp);
  const [argsText, setArgsText] = useState(selected?.args.join("\n") ?? "");
  const [envText, setEnvText] = useState(selected ? JSON.stringify(selected.env, null, 2) : "{}");
  const [secretSlot, setSecretSlot] = useState("");
  const [secret, setSecret] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const choose = (server?: McpServer) => {
    setSelectedId(server?.id ?? "new");
    setDraft(server ? mcpWritePayload(server) : emptyMcp);
    setArgsText(server?.args.join("\n") ?? "");
    setEnvText(JSON.stringify(server?.env ?? {}, null, 2));
    setSecret("");
    setSecretSlot("");
    setResult("");
  };

  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setResult("");
    try {
      const env = JSON.parse(envText) as Record<string, string>;
      const payload: McpServerWrite = draft.transport === "streamable_http"
        ? { ...draft, url: draft.url || null, command: null, args: [], working_directory: null, env }
        : { ...draft, url: null, command: draft.command || null, args: argsText.split("\n").map((value) => value.trim()).filter(Boolean), working_directory: draft.working_directory || null, env };
      const saved = selected ? await updateMcpServer(selected.id, payload) : await createMcpServer(payload);
      setSelectedId(saved.id); setResult("已保存。"); await refresh();
    } catch (cause) { setResult(cause instanceof Error ? cause.message : "保存失败。"); }
    finally { setBusy(false); }
  };

  const operate = async (operation: () => Promise<{ status: string; code: string | null; tool_count?: number | null }>, label: string) => {
    try { const value = await operation(); setResult(`${label}：${statusLabel(value.status)}${value.tool_count != null ? ` · ${value.tool_count} 个工具` : ""}${value.code ? ` · ${value.code}` : ""}`); await refresh(); }
    catch (cause) { setResult(cause instanceof Error ? cause.message : `${label}失败。`); }
  };

  return <>
    <ListPane title="MCP 服务器" onNew={() => choose()}>{servers.map((server) => <ItemButton key={server.id} selected={selectedId === server.id} title={server.name} subtitle={server.transport === "stdio" ? server.command || "stdio" : server.url || "HTTP"} onClick={() => choose(server)} status={server.enabled ? server.last_test_status || "enabled" : "disabled"} />)}</ListPane>
    <DetailPane title={selected ? selected.name : "新建 MCP 服务器"} subtitle="连接 HTTP 或已明确确认的本地 stdio 工具">
      <form onSubmit={(event) => void save(event)} className="space-y-5">
        <div className="grid grid-cols-2 gap-4"><Field label="名称"><input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className="config-input" /></Field><Field label="传输方式"><select value={draft.transport} onChange={(e) => setDraft({ ...draft, transport: e.target.value as McpServerWrite["transport"] })} className="config-input"><option value="streamable_http">流式 HTTP</option><option value="stdio">本地 stdio</option></select></Field></div>
        {draft.transport === "streamable_http" ? <Field label="服务器 URL"><input required type="url" value={draft.url ?? ""} onChange={(e) => setDraft({ ...draft, url: e.target.value })} className="config-input" /></Field> : <><Field label="命令"><input required value={draft.command ?? ""} onChange={(e) => setDraft({ ...draft, command: e.target.value })} className="config-input" /></Field><Field label="参数（每行一个）"><textarea value={argsText} onChange={(e) => setArgsText(e.target.value)} className="config-input min-h-24" /></Field><Field label="工作目录"><input value={draft.working_directory ?? ""} onChange={(e) => setDraft({ ...draft, working_directory: e.target.value })} className="config-input" /></Field></>}
        <Field label="非敏感环境变量（JSON 对象）"><textarea value={envText} onChange={(e) => setEnvText(e.target.value)} className="config-input min-h-24 font-mono text-xs" /></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> 已启用</label>
        {selected && <div className="rounded-lg border border-line bg-[#fbfdfc] p-4"><div className="text-sm font-semibold">敏感密钥槽位</div><p className="mt-1 text-xs text-muted">请使用具名密钥槽位，不要在 URL 或环境变量中填写凭据。</p><div className="mt-3 grid grid-cols-[1fr_1fr_auto] gap-2"><input aria-label="密钥槽位" placeholder="槽位名称" value={secretSlot} onChange={(e) => setSecretSlot(e.target.value)} className="config-input" /><input aria-label="密钥值" type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} className="config-input" /><button type="button" disabled={!secretSlot.trim() || !secret} className="rounded-lg bg-ink px-3 text-xs font-semibold text-white disabled:opacity-50" onClick={() => void saveMcpSecret(selected.id, secretSlot.trim(), secret).then((status) => { setResult(`已保存密钥 ${status.masked_value}。`); setSecret(""); }).catch((cause) => setResult(cause instanceof Error ? cause.message : "保存密钥失败。"))}>保存</button></div>{secretSlot.trim() && <button type="button" className="mt-3 text-xs font-semibold text-coral" onClick={() => void removeMcpSecret(selected.id, secretSlot.trim()).then(() => setResult("已删除已保存的密钥。")).catch((cause) => setResult(cause instanceof Error ? cause.message : "删除密钥失败。"))}>删除此槽位</button>}</div>}
        {selected && <div className="flex flex-wrap gap-2"><Action onClick={() => void operate(() => testMcpServer(selected.id), "连接测试")}>测试</Action><Action onClick={() => void operate(() => discoverMcpServer(selected.id), "工具发现")}>发现工具</Action>{selected.transport === "stdio" && <Action onClick={() => { if (window.confirm(`确认信任此本地命令？\n\n${selected.command ?? ""} ${selected.args.join(" ")}`)) void trustMcpServer(selected.id).then(() => { setResult("已信任本地 stdio 命令。"); return refresh(); }).catch((cause) => setResult(cause instanceof Error ? cause.message : "确认信任失败。")); }}>确认 stdio 信任</Action>}</div>}
        {selected && <section className="rounded-lg border border-line"><div className="border-b border-line px-4 py-3"><div className="text-sm font-semibold">已发现的工具</div><div className="text-xs text-muted">已持久化 {(selected.tools ?? []).length} 个工具</div></div>{(selected.tools ?? []).length ? (selected.tools ?? []).map((tool) => <label key={tool.id || tool.name} className="flex items-start justify-between gap-4 border-b border-line px-4 py-3 last:border-b-0"><span><span className="block text-sm font-semibold">{tool.title || tool.name}</span><span className="mt-1 block text-xs text-muted">{tool.description || tool.name}</span></span><input type="checkbox" checked={tool.enabled} onChange={(e) => void setMcpToolEnabled(selected.id, tool.name, e.target.checked).then(refresh).catch((cause) => setResult(cause instanceof Error ? cause.message : "更新工具失败。"))} /></label>) : <div className="px-4 py-5 text-sm text-muted">运行工具发现以加载服务器的实际工具目录。</div>}</section>}
        <Footer busy={busy} result={result} onDelete={selected ? () => { if (window.confirm(`删除 ${selected.name}？`)) void removeMcpServer(selected.id).then(() => { setSelectedId("new"); return refresh(); }).catch((cause) => setResult(cause instanceof Error ? cause.message : "删除失败。")); } : undefined} />
      </form>
    </DetailPane>
  </>;
}

function ListPane({ title, onNew, children }: { title: string; onNew: () => void; children: ReactNode }) { return <section className="border-r border-line bg-white"><div className="flex h-16 items-center justify-between border-b border-line px-4"><h2 className="text-sm font-semibold">{title}</h2><button type="button" onClick={onNew} className="rounded-lg bg-teal px-3 py-2 text-xs font-semibold text-white">新建</button></div><div className="space-y-1 p-2">{children}</div></section>; }
function DetailPane({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) { return <section className="overflow-y-auto bg-[#fbfdfc]"><div className="border-b border-line bg-white px-7 py-4"><h1 className="text-lg font-semibold">{title}</h1><p className="mt-1 text-xs text-muted">{subtitle}</p></div><div className="mx-auto max-w-3xl p-7">{children}</div></section>; }
function ItemButton({ selected, title, subtitle, status, onClick }: { selected: boolean; title: string; subtitle: string; status: string; onClick: () => void }) { return <button type="button" onClick={onClick} className={`w-full rounded-lg border px-3 py-3 text-left ${selected ? "border-teal bg-tealSoft" : "border-transparent hover:bg-[#f4f8f8]"}`}><span className="flex items-center justify-between gap-3"><span className="truncate text-sm font-semibold">{title}</span><span className="rounded-full bg-white px-2 py-1 text-[10px] text-muted">{statusLabel(status)}</span></span><span className="mt-1 block truncate text-xs text-muted">{subtitle}</span></button>; }
function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="block text-sm font-semibold"><span className="mb-2 block">{label}</span>{children}</label>; }
function Action({ children, onClick }: { children: ReactNode; onClick: () => void }) { return <button type="button" onClick={onClick} className="rounded-lg border border-line bg-white px-3 py-2 text-xs font-semibold text-teal hover:bg-tealSoft">{children}</button>; }
function Footer({ busy, result, onDelete }: { busy: boolean; result: string; onDelete?: () => void }) { return <div className="flex items-center justify-between border-t border-line pt-5"><div>{result && <p role="status" className="text-xs text-muted">{result}</p>}{onDelete && <button type="button" onClick={onDelete} className="mt-2 text-xs font-semibold text-coral">删除</button>}</div><button disabled={busy} type="submit" className="flex items-center gap-2 rounded-lg bg-teal px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"><MdBuild />{busy ? "正在保存…" : "保存更改"}</button></div>; }
