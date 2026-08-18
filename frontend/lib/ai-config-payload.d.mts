import type { McpServer, McpServerWrite, ModelProfile, ModelProfileWrite, PromptSkill, PromptSkillWrite } from "../features/ai-config/types";

export function modelWritePayload(value: ModelProfile): ModelProfileWrite;
export function skillWritePayload(value: PromptSkill): PromptSkillWrite;
export function mcpWritePayload(value: McpServer): McpServerWrite;
