export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** The server's raw `detail` code when it differs from the shown message. */
    public code: string = message,
  ) {
    super(message);
  }
}

/** Server detail codes that deserve a sentence instead of the raw code. */
const explained: Record<string, string> = {
  package_inactive:
    "This area is not part of your organisation's active service packages. Ask your administrator to activate it in Services.",
  proposal_stale:
    "The record changed after this was proposed. Nothing was changed; review it and ask again.",
  proposal_consumed: "This proposal was already decided. Nothing more was changed.",
  proposal_expired: "This proposal expired. Nothing was changed; ask again if you still need it.",
  agent_disabled:
    "This assistant is switched off for your organisation. An administrator can turn it on in its settings.",
  planner_unavailable: "The assistant is unavailable right now. Try again in a moment.",
  mailbox_not_connected: "Connect your Gmail in the Email tab first.",
  whatsapp_number_in_use: "That WhatsApp number is already connected to another company.",
};

async function decode<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code = message;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        code = body.detail;
        message = explained[code] ?? code;
      }
    } catch {
      /* Non-JSON gateway failures keep their HTTP status. */
    }
    throw new ApiError(response.status, message, code);
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  if (!path.startsWith("/console/v1/"))
    throw new Error("Only the CSAV2 console API is adopted");
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = await decode<{ csrf_token: string }>(
      await fetch("/console/v1/auth/csrf", {
        credentials: "same-origin",
        cache: "no-store",
        signal: init.signal,
      }),
    );
    headers.set("X-CSRF-Token", csrf.csrf_token);
  }
  if (init.body instanceof FormData) {
    // The browser supplies the multipart boundary.
    headers.delete("Content-Type");
  } else if (init.body instanceof Blob) {
    headers.set("Content-Type", init.body.type || "application/octet-stream");
  } else if (init.body !== undefined) headers.set("Content-Type", "application/json");
  return decode<T>(
    await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    }),
  );
}

export const post = <T = void>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T = void>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T = void>(path: string) =>
  api<T>(path, { method: "DELETE" });
