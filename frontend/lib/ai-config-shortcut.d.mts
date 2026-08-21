type ShortcutEvent = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "defaultPrevented" | "target">;

export function shouldNavigateToAiConfig(event: ShortcutEvent): boolean;
