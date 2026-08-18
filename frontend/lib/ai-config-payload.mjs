export const modelWritePayload = ({ name, capability, provider, base_url, model_name, dimensions, enabled }) => ({
  name, capability, provider, base_url, model_name, dimensions, enabled,
});

export const skillWritePayload = ({ name, description, instructions, enabled, default_enabled, model_profile_id }) => ({
  name, description, instructions, enabled, default_enabled, model_profile_id,
});

export const mcpWritePayload = ({ name, transport, url, command, args, working_directory, env, enabled }) => ({
  name, transport, url, command, args, working_directory, env, enabled,
});
