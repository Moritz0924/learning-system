import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextVitals,
  globalIgnores([
    ".next/**",
    ".next-e2e/**",
    "playwright-report/**",
    "test-results/**",
    "next-env.d.ts",
    "**/*.d.mts"
  ])
]);
