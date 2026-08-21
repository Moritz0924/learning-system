export type AiCapability = "chat" | "reasoning" | "vision" | "embedding";

export type ModelProfileWrite = {
  name: string;
  capability: AiCapability;
  provider: "openai_compatible";
  base_url: string;
  model_name: string;
  dimensions: number | null;
  enabled: boolean;
};

export type ModelProfile = ModelProfileWrite & {
  id: string;
  last_test_status: string | null;
};

export type CapabilityBinding = {
  id: string;
  capability: AiCapability;
  model_profile_id: string;
};

export type PromptSkillWrite = {
  name: string;
  description: string;
  instructions: string;
  enabled: boolean;
  default_enabled: boolean;
  model_profile_id: string | null;
};

export type PromptSkill = PromptSkillWrite & { id: string };

export type McpTransport = "streamable_http" | "stdio";

export type McpTool = {
  id: string;
  name: string;
  title: string | null;
  description: string;
  input_schema?: Record<string, unknown>;
  annotations?: Record<string, unknown>;
  enabled: boolean;
};

export type McpServerWrite = {
  name: string;
  transport: McpTransport;
  url: string | null;
  command: string | null;
  args: string[];
  working_directory: string | null;
  env: Record<string, string>;
  enabled: boolean;
};

export type McpServer = McpServerWrite & {
  id: string;
  trust_fingerprint: string | null;
  trusted_at: string | null;
  last_test_status: string | null;
  tools?: McpTool[];
};

export type OperationResult = {
  status: "success" | "failed";
  code: string | null;
  tool_count?: number | null;
};

export type SecretStatus = {
  configured: boolean;
  masked_value: string;
};

export type ToolApproval = {
  approval_id: string;
  run_id: string;
  server: { id: string; name: string };
  tool_name: string;
  arguments: Record<string, unknown>;
  status: "pending" | "executing" | "rejected" | "completed" | "failed";
  result_summary: Record<string, unknown>;
};
