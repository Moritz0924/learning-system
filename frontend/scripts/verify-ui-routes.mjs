import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const appDir = join(root, "app");

const routes = ["diagnosis", "today", "tutor", "assessment", "path", "progress", "settings", "ai-config"];
const routeExists = (route) => existsSync(join(appDir, route, "page.tsx")) || existsSync(join(appDir, "(learning)", route, "page.tsx"));
const missingRoutes = routes.filter((route) => !routeExists(route));

const requiredFiles = [
  join(root, "components", "learning-shell.tsx"),
  join(root, "components", "learning-provider.tsx"),
  join(root, "lib", "learning-data.ts"),
  join(root, "features", "memory", "types.ts"),
  join(root, "features", "memory", "memory-api.ts"),
  join(root, "features", "memory", "memory-settings-panel.tsx"),
  join(root, "features", "ai-config", "ai-config-console.tsx"),
  join(root, "features", "ai-config", "ai-config-api.ts")
];
const missingFiles = requiredFiles.filter((file) => !existsSync(file));

const shellPath = join(root, "components", "learning-shell.tsx");
const shellSource = existsSync(shellPath) ? readFileSync(shellPath, "utf8") : "";
const pageSource = readFileSync(join(appDir, "page.tsx"), "utf8");
const providerPath = join(root, "components", "learning-provider.tsx");
const providerSource = existsSync(providerPath) ? readFileSync(providerPath, "utf8") : "";
const pagesPath = join(root, "components", "learning-pages.tsx");
const pagesSource = existsSync(pagesPath) ? readFileSync(pagesPath, "utf8") : "";

const failures = [
  missingRoutes.length ? `missing route pages: ${missingRoutes.join(", ")}` : "",
  missingFiles.length ? `missing shared files: ${missingFiles.map((file) => file.replace(root, "")).join(", ")}` : "",
  !shellSource.includes("usePathname") ? "learning shell must use usePathname for selected navigation" : "",
  !shellSource.includes("next/link") ? "learning shell must render route navigation with next/link" : "",
  !shellSource.includes("shouldNavigateToAiConfig") ? "learning shell must install the AI config keyboard shortcut" : "",
  shellSource.includes("memory_declaration") ? "quick tutor chat must never submit memory declarations" : "",
  !providerSource.includes("pendingMemoryRequestRef") ? "memory declaration retries must retain a pending request UUID" : "",
  !providerSource.includes("memory_declaration") ? "main tutor requests must support the public memory declaration" : "",
  !pagesSource.includes('data-testid="memory-declaration-toggle"') ? "tutor page must expose an opt-in memory declaration toggle" : "",
  !pagesSource.includes("MemorySettingsPanel") ? "settings page must render memory privacy and lifecycle controls" : "",
  pageSource.includes("useState(\"path\")") || pageSource.includes("activeNav")
    ? "root page should not own fake activeNav navigation state"
    : ""
].filter(Boolean);

if (failures.length) {
  console.error(`UI route verification failed:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}

console.log("UI route verification passed.");
