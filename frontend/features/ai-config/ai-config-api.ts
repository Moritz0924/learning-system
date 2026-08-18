import { deleteRequest, getRequest, postRequest, putRequest } from "@/lib/api";

import type {
  AiCapability,
  CapabilityBinding,
  McpServer,
  McpServerWrite,
  McpTool,
  ModelProfile,
  ModelProfileWrite,
  OperationResult,
  PromptSkill,
  PromptSkillWrite,
  SecretStatus,
  ToolApproval,
} from "./types";

export const listModels = () => getRequest<{ models: ModelProfile[] }>("/api/config/models");
export const createModel = (payload: ModelProfileWrite) => postRequest<ModelProfile>("/api/config/models", payload);
export const updateModel = (id: string, payload: ModelProfileWrite) => putRequest<ModelProfile>(`/api/config/models/${encodeURIComponent(id)}`, payload);
export const removeModel = (id: string) => deleteRequest<void>(`/api/config/models/${encodeURIComponent(id)}`);
export const testModel = (id: string) => postRequest<OperationResult>(`/api/config/models/${encodeURIComponent(id)}/test`, {});
export const saveModelSecret = (id: string, value: string) => putRequest<SecretStatus>(`/api/config/models/${encodeURIComponent(id)}/secret`, { value });
export const removeModelSecret = (id: string) => deleteRequest<void>(`/api/config/models/${encodeURIComponent(id)}/secret`);

export const listBindings = () => getRequest<{ bindings: CapabilityBinding[] }>("/api/config/bindings");
export const bindModel = (capability: AiCapability, modelProfileId: string) => putRequest<CapabilityBinding>(`/api/config/bindings/${capability}`, { model_profile_id: modelProfileId });
export const unbindModel = (capability: AiCapability) => deleteRequest<void>(`/api/config/bindings/${capability}`);

export const listSkills = () => getRequest<{ skills: PromptSkill[] }>("/api/config/skills");
export const createSkill = (payload: PromptSkillWrite) => postRequest<PromptSkill>("/api/config/skills", payload);
export const updateSkill = (id: string, payload: PromptSkillWrite) => putRequest<PromptSkill>(`/api/config/skills/${encodeURIComponent(id)}`, payload);
export const removeSkill = (id: string) => deleteRequest<void>(`/api/config/skills/${encodeURIComponent(id)}`);

export const listMcpServers = () => getRequest<{ mcp_servers: McpServer[] }>("/api/config/mcp-servers");
export const createMcpServer = (payload: McpServerWrite) => postRequest<McpServer>("/api/config/mcp-servers", payload);
export const updateMcpServer = (id: string, payload: McpServerWrite) => putRequest<McpServer>(`/api/config/mcp-servers/${encodeURIComponent(id)}`, payload);
export const removeMcpServer = (id: string) => deleteRequest<void>(`/api/config/mcp-servers/${encodeURIComponent(id)}`);
export const saveMcpSecret = (id: string, slot: string, value: string) => putRequest<SecretStatus>(`/api/config/mcp-servers/${encodeURIComponent(id)}/secrets/${encodeURIComponent(slot)}`, { value });
export const removeMcpSecret = (id: string, slot: string) => deleteRequest<void>(`/api/config/mcp-servers/${encodeURIComponent(id)}/secrets/${encodeURIComponent(slot)}`);
export const trustMcpServer = (id: string) => postRequest<{ trust_fingerprint: string; trusted_at: string }>(`/api/config/mcp-servers/${encodeURIComponent(id)}/trust`, {});
export const testMcpServer = (id: string) => postRequest<OperationResult>(`/api/config/mcp-servers/${encodeURIComponent(id)}/test`, {});
export const discoverMcpServer = (id: string) => postRequest<OperationResult>(`/api/config/mcp-servers/${encodeURIComponent(id)}/discover`, {});
export const setMcpToolEnabled = (serverId: string, toolName: string, enabled: boolean) => putRequest<McpTool>(`/api/config/mcp-servers/${encodeURIComponent(serverId)}/tools/${encodeURIComponent(toolName)}`, { enabled });

export const listToolApprovals = (threadId: string) => getRequest<{ approvals: ToolApproval[] }>(`/api/tutor/tool-approvals?thread_id=${encodeURIComponent(threadId)}`);
