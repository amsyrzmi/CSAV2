import { redirect } from "next/navigation";

/** The Sandbox assistant folded into Overview; old links keep their tenant. */
export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const clientId = (await searchParams).client_id;
  const tenant = typeof clientId === "string" ? clientId.trim() : "";
  redirect(tenant ? `/portal?${new URLSearchParams({ client_id: tenant })}` : "/portal");
}
