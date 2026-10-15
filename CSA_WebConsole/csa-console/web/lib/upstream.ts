// Server configuration only: never expose this value with a NEXT_PUBLIC prefix.
export function consoleOrigin(): string {
  const value = process.env.CSA_CONSOLE_API_ORIGIN;
  if (!value) throw new Error("CSA_CONSOLE_API_ORIGIN is required");
  const url = new URL(value);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "CSA_CONSOLE_API_ORIGIN must be an HTTP(S) origin without credentials, path or query",
    );
  }
  return url.origin;
}
