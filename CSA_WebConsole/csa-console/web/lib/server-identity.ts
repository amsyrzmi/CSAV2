import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { consoleOrigin } from "./upstream";
import type { Client, Me } from "./identity-contracts";
import type { PackageState } from "./services-api";

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

export async function activePackages(): Promise<PackageState[]> {
  const jar = await cookies();
  const response = await fetch(`${consoleOrigin()}/console/v1/services/active`, {
    headers: { cookie: jar.toString() },
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
  if (response.status === 401) redirect("/login");
  // A platform admin without a tenant has no packages to report.
  if (response.status === 400 || response.status === 403) return [];
  if (!response.ok)
    throw new Error(`CSAV2 services service returned ${response.status}`);
  return ((await response.json()) as { packages: PackageState[] }).packages;
}

/**
 * The signed-in tenant's name for the user menu, or null when it cannot be read. This is
 * decoration, so a failed or slow profile read must never break the layout; only a 401 keeps
 * the sign-in redirect, as everywhere else.
 */
export async function optionalTenantName(): Promise<string | null> {
  const jar = await cookies();
  let response: Response;
  try {
    response = await fetch(`${consoleOrigin()}/console/v1/profile`, {
      headers: { cookie: jar.toString() },
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
  } catch {
    return null;
  }
  if (response.status === 401) redirect("/login");
  if (!response.ok) return null;
  try {
    const name = ((await response.json()) as Partial<Client>).name;
    return typeof name === "string" && name.trim() ? name : null;
  } catch {
    return null;
  }
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
