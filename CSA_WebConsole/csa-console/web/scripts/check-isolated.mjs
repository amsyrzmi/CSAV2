/** Verify only reviewed frontend source. Never copy or load an environment file. */
import {
  cp,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  symlink,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const stage = await mkdtemp(path.join(tmpdir(), "csa-console-check-"));
const files = [
  "package.json",
  "package-lock.json",
  "tsconfig.json",
  "next.config.ts",
  "proxy.ts",
  "postcss.config.mjs",
  "eslint.config.mjs",
  "lib/api.ts",
  "lib/identity-contracts.ts",
  "lib/identity-api.ts",
  "lib/server-identity.ts",
  "lib/upstream.ts",
  "components/AppShell.tsx",
  "components/ui/auth-card.tsx",
  "components/ui/password-input.tsx",
  "components/ui/input.ts",
  "public/brand/fourstacks-mark.svg",
  "tests/identity-transport.test.mjs",
];
async function addTree(directory) {
  for (const entry of await readdir(path.join(source, directory), {
    withFileTypes: true,
  })) {
    if (
      entry.isSymbolicLink() ||
      entry.name.startsWith(".") ||
      entry.name.includes("env")
    )
      throw new Error(`Unexpected source entry in ${directory}`);
    const relative = path.join(directory, entry.name);
    if (entry.isDirectory()) await addTree(relative);
    else if (/\.(tsx?|css|ico)$/.test(entry.name)) files.push(relative);
    else throw new Error(`Unreviewed file: ${relative}`);
  }
}
await addTree("app");
await addTree("components/console");
for (const relative of files) {
  await mkdir(path.dirname(path.join(stage, relative)), { recursive: true });
  await cp(path.join(source, relative), path.join(stage, relative));
}
await symlink(
  path.join(source, "node_modules"),
  path.join(stage, "node_modules"),
  "dir",
);
const manifest = JSON.parse(
  await readFile(path.join(stage, "package.json"), "utf8"),
);
const next = JSON.parse(
  await readFile(path.join(source, "node_modules/next/package.json"), "utf8"),
);
if (manifest.dependencies.next !== next.version)
  throw new Error("Installed Next version differs from the reviewed pin");
const environment = {
  PATH: process.env.PATH,
  NODE_ENV: "production",
  NEXT_TELEMETRY_DISABLED: "1",
  CSA_CONSOLE_API_ORIGIN: "http://127.0.0.1:18080",
};
const commands = [
  ["identity transport tests", "../tests/identity-transport.test.mjs"],
  ["lint", "eslint/bin/eslint.js", "."],
  ["typegen", "next/dist/bin/next", "typegen"],
  ["typecheck", "typescript/bin/tsc", "--noEmit"],
  ["build", "next/dist/bin/next", "build", "--webpack"],
];
console.log(`Isolated source directory: ${stage}`);
for (const [label, executable, ...args] of commands) {
  console.log(`Running ${label}`);
  const result = spawnSync(
    process.execPath,
    [
      executable.startsWith("../tests/")
        ? path.join(stage, executable.slice(3))
        : path.join(source, "node_modules", executable),
      ...args,
    ],
    { cwd: stage, env: environment, stdio: "inherit" },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
async function verifyTraces(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) await verifyTraces(target);
    else if (entry.name.endsWith(".nft.json")) {
      const trace = JSON.parse(await readFile(target, "utf8"));
      for (const file of trace.files ?? []) {
        const absolute = path.resolve(path.dirname(target), file);
        if (
          /(?:^|\/)\.env(?:[./]|$)|\/\.git\/|\/\.backups\/|\/csa-console\/api\//.test(
            absolute,
          )
        )
          throw new Error("Build trace crossed the reviewed frontend boundary");
      }
    }
  }
}
await verifyTraces(path.join(stage, ".next"));
console.log(
  "Build traces exclude environment files, nested Git, backups and the legacy API.",
);
