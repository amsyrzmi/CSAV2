import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { consoleOrigin } from "./upstream";
import type { Client, Me } from "./identity-contracts";

export async function requireIdentity(): Promise<Me> {
  const jar = await cookies();
  if (!jar.has("csa_session")) redirect("/login");
  const response = await fetch(`${consoleOrigin()}/console/v1/auth/me`, {
    headers: { cookie: jar.toString() },
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
  if (response.status === 401) redirect("/login");
  if (!response.ok)
    throw new Error(`CSAV2 identity service returned ${response.status}`);
  return response.json() as Promise<Me>;
}

export async function requireOwnProfile(): Promise<Client> {
  const jar = await cookies();
  const response = await fetch(`${consoleOrigin()}/console/v1/profile`, {
    headers: { cookie: jar.toString() },
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
  if (response.status === 401) redirect("/login");
  if (!response.ok)
    throw new Error(`CSAV2 tenant service returned ${response.status}`);
  return response.json() as Promise<Client>;
}
