export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function decode<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      )
        message = body.detail;
    } catch {
      /* Non-JSON gateway failures keep their HTTP status. */
    }
    throw new ApiError(response.status, message);
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
      }),
    );
    headers.set("X-CSRF-Token", csrf.csrf_token);
  }
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
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
