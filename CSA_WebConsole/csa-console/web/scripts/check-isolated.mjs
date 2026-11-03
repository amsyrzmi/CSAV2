/** Verify only reviewed frontend source. Never copy or load an environment file. */
import {
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const contextOnly = process.argv[2] === "--context";
const install = process.argv[2] === "--install";
if (
  (contextOnly && process.argv.length !== 4) ||
  (!contextOnly && process.argv.length > (install ? 3 : 2))
)
  throw new Error("Usage: check-isolated.mjs [--install | --context NEW_DIRECTORY]");
const stage = contextOnly
  ? path.resolve(process.argv[3])
  : await mkdtemp(path.join(tmpdir(), "csa-console-check-"));
if (contextOnly && !path.relative(source, stage).startsWith(`..${path.sep}`))
  throw new Error("Release context must be outside the frontend checkout");
if (contextOnly) await mkdir(stage);
try {
  const files = [
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "next.config.ts",
    "proxy.ts",
    "postcss.config.mjs",
    "eslint.config.mjs",
    "lib/api.ts",
    "lib/business.ts",
    "lib/hr-api.ts",
    "lib/hr-access-api.ts",
    "lib/hr-access-copy.ts",
    "lib/hr-paths.ts",
    "lib/hr-people-api.ts",
    "lib/hr-people.ts",
    "lib/hr-records-api.ts",
    "lib/hr-records.ts",
    "lib/hr-owner.ts",
    "lib/hr-person-card.ts",
    "lib/hr-pickers.ts",
    "lib/hr-requests.ts",
    "lib/invite-access.ts",
    "lib/email-api.ts",
    "lib/email-recipients.ts",
    "lib/services-api.ts",
    "lib/services-view.ts",
    "lib/avatar-transition.ts",
    "lib/subscriptions-api.ts",
    "lib/subscriptions-view.ts",
    "lib/channels-api.ts",
    "lib/navigation.ts",
    "lib/hr-voice.ts",
    "lib/voice-session.ts",
    "lib/identity-contracts.ts",
    "lib/identity-api.ts",
    "lib/server-identity.ts",
    "lib/upstream.ts",
    "lib/sandbox-chat.ts",
    "lib/password-messages.ts",
    "lib/password-rules.ts",
    "lib/overview-strip.ts",
    "lib/workspace-api.ts",
    "lib/page-context.tsx",
    "lib/page-context-state.ts",
    "lib/assistant-view.ts",
    "lib/assistant-copy.ts",
    "lib/agent-avatars.ts",
    "lib/customer-identity.ts",
    "lib/motion.ts",
    "lib/inbox-shortcuts.ts",
    "lib/inbox-api.ts",
    "lib/inbox-thread.ts",
    "lib/theme.ts",
    "lib/auth-copy.ts",
    "lib/shell-state.ts",
    "lib/pixel-avatars-data.ts",
    "lib/pixel-avatars.ts",
    "lib/chips.ts",
    "lib/date-range.ts",
    "lib/business-view.ts",
    "lib/activity-view.ts",
    "lib/knowledge-view.ts",
    "lib/email-view.ts",
    "lib/team-view.ts",
    "lib/workspace-paths.mjs",
    "lib/workspace-paths.d.mts",
    "components/AppShell.tsx",
    "components/ui/auth-card.tsx",
    "components/ui/FourStacksLogo.tsx",
    "components/ui/password-input.tsx",
    "components/ui/input.ts",
    "components/ui/kit.tsx",
    "public/brand/fourstacks-mark.svg",
    "public/fourstacks/agents/support.svg",
    "public/fourstacks/agents/knowledge.svg",
    "public/fourstacks/agents/orders.svg",
    "public/fourstacks/agents/tickets.svg",
    "public/fourstacks/agents/ecommerce.svg",
    "public/fourstacks/agents/invoices.svg",
    "public/fourstacks/agents/assistant.svg",
    "public/fourstacks/agents/hr.svg",
    "public/fourstacks/agents/email.svg",
    "public/fourstacks/agents/subscriptions.svg",
    "public/fourstacks/agents/bookings.svg",
    "public/fourstacks/agents/einvoicing.svg",
    "tests/identity-transport.test.mjs",
    "tests/hr-transport.test.mjs",
    "tests/hr-access-api.test.mjs",
    "tests/hr-paths.test.mjs",
    "tests/hr-people-api.test.mjs",
    "tests/hr-people.test.mjs",
    "tests/hr-records.test.mjs",
    "tests/hr-owner.test.mjs",
    "tests/hr-person-card.test.mjs",
    "tests/hr-pickers.test.mjs",
    "tests/hr-requests.test.mjs",
    "tests/invite-access.test.mjs",
    "tests/voice-session.test.mjs",
    "tests/email-transport.test.mjs",
    "tests/email-recipients.test.mjs",
    "tests/services-api.test.mjs",
    "tests/services-view.test.mjs",
    "tests/avatar-transition.test.mjs",
    "tests/subscriptions-api.test.mjs",
    "tests/subscriptions-view.test.mjs",
    "tests/channels-transport.test.mjs",
    "tests/nav-visibility.test.mjs",
    "tests/sandbox-chat.test.mjs",
    "tests/password-messages.test.mjs",
    "tests/password-rules.test.mjs",
    "tests/overview-strip.test.mjs",
    "tests/workspace-paths.test.mjs",
    "tests/workspace-api.test.mjs",
    "tests/page-context.test.mjs",
    "tests/assistant-view.test.mjs",
    "tests/assistant-copy.test.mjs",
    "tests/agent-avatars.test.mjs",
    "tests/customer-identity.test.mjs",
    "tests/motion.test.mjs",
    "tests/inbox-shortcuts.test.mjs",
    "tests/inbox-api.test.mjs",
    "tests/inbox-thread.test.mjs",
    "tests/theme.test.mjs",
    "tests/auth-copy.test.mjs",
    "tests/theme-contrast.test.mjs",
    "tests/theme-literals.test.mjs",
    "tests/shell-state.test.mjs",
    "tests/pixel-avatars-data.test.mjs",
    "tests/pixel-avatars.test.mjs",
    "tests/chips.test.mjs",
    "tests/date-range.test.mjs",
    "tests/business-view.test.mjs",
    "tests/activity-view.test.mjs",
    "tests/knowledge-view.test.mjs",
    "tests/email-view.test.mjs",
    "tests/team-view.test.mjs",
    "tests/public-assets.test.mjs",
    "tests/release-runtime.test.mjs",
    "scripts/release-runtime.mjs",
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
    for (let parent = path.dirname(relative); parent !== "."; parent = path.dirname(parent)) {
      if ((await lstat(path.join(source, parent))).isSymbolicLink())
        throw new Error(`Symlink source directory: ${parent}`);
    }
    if (!(await lstat(path.join(source, relative))).isFile())
      throw new Error(`Release input is not a regular file: ${relative}`);
    await mkdir(path.dirname(path.join(stage, relative)), { recursive: true });
    await cp(path.join(source, relative), path.join(stage, relative));
  }
  if (contextOnly) {
    if (!(await lstat(path.join(source, "Dockerfile"))).isFile())
      throw new Error("Dockerfile must be a regular file");
    await cp(path.join(source, "Dockerfile"), path.join(stage, "Dockerfile"));
  } else {
    if (install) {
      const result = spawnSync("npm", ["ci", "--ignore-scripts", "--no-audit", "--no-fund"], {
        cwd: stage,
        env: { PATH: process.env.PATH, HOME: process.env.HOME, NEXT_TELEMETRY_DISABLED: "1" },
        stdio: "inherit",
      });
      if (result.error) throw result.error;
      if (result.status !== 0) throw new Error(`Isolated install failed: ${result.status}`);
    } else {
      await symlink(path.join(source, "node_modules"), path.join(stage, "node_modules"), "dir");
    }
    const dependencies = path.join(stage, "node_modules");
    const manifest = JSON.parse(
      await readFile(path.join(stage, "package.json"), "utf8"),
    );
    const next = JSON.parse(
      await readFile(path.join(dependencies, "next/package.json"), "utf8"),
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
      ["HR transport tests", "../tests/hr-transport.test.mjs"],
      ["HR access transport tests", "../tests/hr-access-api.test.mjs"],
      ["HR paths tests", "../tests/hr-paths.test.mjs"],
      ["HR people transport tests", "../tests/hr-people-api.test.mjs"],
      ["HR people helper tests", "../tests/hr-people.test.mjs"],
      ["HR records tests", "../tests/hr-records.test.mjs"],
      ["HR owner tests", "../tests/hr-owner.test.mjs"],
      ["HR person card tests", "../tests/hr-person-card.test.mjs"],
      ["HR picker tests", "../tests/hr-pickers.test.mjs"],
      ["HR request action tests", "../tests/hr-requests.test.mjs"],
      ["invite access tests", "../tests/invite-access.test.mjs"],
      ["voice session tests", "../tests/voice-session.test.mjs"],
      ["email transport tests", "../tests/email-transport.test.mjs"],
      ["email recipient tests", "../tests/email-recipients.test.mjs"],
      ["services transport tests", "../tests/services-api.test.mjs"],
      ["services view tests", "../tests/services-view.test.mjs"],
      ["avatar transition tests", "../tests/avatar-transition.test.mjs"],
      ["subscriptions transport tests", "../tests/subscriptions-api.test.mjs"],
      ["subscriptions view tests", "../tests/subscriptions-view.test.mjs"],
      ["channels transport tests", "../tests/channels-transport.test.mjs"],
      ["navigation tests", "../tests/nav-visibility.test.mjs"],
      ["sandbox state tests", "../tests/sandbox-chat.test.mjs"],
      ["password message tests", "../tests/password-messages.test.mjs"],
      ["password rule tests", "../tests/password-rules.test.mjs"],
      ["overview strip tests", "../tests/overview-strip.test.mjs"],
      ["workspace path tests", "../tests/workspace-paths.test.mjs"],
      ["workspace API tests", "../tests/workspace-api.test.mjs"],
      ["page context tests", "../tests/page-context.test.mjs"],
      ["assistant view tests", "../tests/assistant-view.test.mjs"],
      ["assistant copy tests", "../tests/assistant-copy.test.mjs"],
      ["agent avatar tests", "../tests/agent-avatars.test.mjs"],
      ["customer identity tests", "../tests/customer-identity.test.mjs"],
      ["motion tests", "../tests/motion.test.mjs"],
      ["inbox shortcut tests", "../tests/inbox-shortcuts.test.mjs"],
      ["inbox API tests", "../tests/inbox-api.test.mjs"],
      ["inbox thread tests", "../tests/inbox-thread.test.mjs"],
      ["theme tests", "../tests/theme.test.mjs"],
      ["auth copy tests", "../tests/auth-copy.test.mjs"],
      ["theme contrast tests", "../tests/theme-contrast.test.mjs"],
      ["theme literal tests", "../tests/theme-literals.test.mjs"],
      ["shell state tests", "../tests/shell-state.test.mjs"],
      ["pixel avatar data tests", "../tests/pixel-avatars-data.test.mjs"],
      ["pixel avatar tests", "../tests/pixel-avatars.test.mjs"],
      ["status chip tests", "../tests/chips.test.mjs"],
      ["date range tests", "../tests/date-range.test.mjs"],
      ["business view tests", "../tests/business-view.test.mjs"],
      ["activity view tests", "../tests/activity-view.test.mjs"],
      ["knowledge view tests", "../tests/knowledge-view.test.mjs"],
      ["email view tests", "../tests/email-view.test.mjs"],
      ["team view tests", "../tests/team-view.test.mjs"],
      ["public asset tests", "../tests/public-assets.test.mjs"],
      ["release origin tests", "../tests/release-runtime.test.mjs"],
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
            : path.join(dependencies, executable),
          ...args,
        ],
        { cwd: stage, env: environment, stdio: "inherit" },
      );
      if (result.error) throw result.error;
      if (result.status !== 0) throw new Error(`${label} failed: ${result.status}`);
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
  }
} finally {
  if (!contextOnly) await rm(stage, { recursive: true, force: true });
}
