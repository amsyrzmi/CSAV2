import { redirect } from "next/navigation";

/** The private chat now lives on Overview; this old link redirects there and keeps the company. */
export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const clientId = (await searchParams).client_id;
  const tenant = typeof clientId === "string" ? clientId.trim() : "";
  redirect(tenant ? `/?${new URLSearchParams({ client_id: tenant })}` : "/");
}
