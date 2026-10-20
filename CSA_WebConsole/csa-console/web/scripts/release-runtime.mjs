import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

export function assertReleaseOrigin(built, runtime) {
  if (!runtime) throw new Error("CSA_CONSOLE_API_ORIGIN is required at runtime");
  const url = new URL(runtime);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username || url.password || url.pathname !== "/" || url.search || url.hash
  )
    throw new Error("CSA_CONSOLE_API_ORIGIN must be a bare HTTP(S) origin");
  if (url.origin !== new URL(built).origin)
    throw new Error("Runtime console origin differs from the build; rebuild the artifact");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  assertReleaseOrigin(
    await readFile(new URL("../console-api-origin", import.meta.url), "utf8"),
    process.env.CSA_CONSOLE_API_ORIGIN,
  );
  await import("../server.js");
}
