import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// M1 adoption boundary. Retained PM/chat components remain inactive reference source.
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "components/pm/**",
    "components/chat/**",
    "components/ActivityFeed.tsx",
    "components/AgentGrid.tsx",
    "components/providers/**",
    "components/ui.ts",
    "components/ui/button.ts",
    "components/ui/state-chip.tsx",
    "components/ui/chart.tsx",
    "components/ui/kit.tsx",
    "lib/chat-api.ts",
    "lib/chat-types.ts",
    "lib/time.ts",
  ]),
]);
