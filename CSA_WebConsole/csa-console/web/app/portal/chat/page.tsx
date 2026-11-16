import { redirect } from "next/navigation";
import { previewHref } from "@/lib/preview-redirect";

/** The private chat is now the customer preview on Services; this old link redirects there and keeps every query parameter (the company included). */
export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  redirect(previewHref("/portal/services", await searchParams));
}
