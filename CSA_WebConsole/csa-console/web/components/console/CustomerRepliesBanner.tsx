"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { bannerLines, readReplies, type CustomerReplies } from "@/lib/customer-replies";
import { Notice } from "./ui";

/** Says so while customers aren't being answered. It never breaks the page it sits on. */
export function CustomerRepliesBanner({ tenantId, servicesHref }: {
  tenantId: string | null; servicesHref: string;
}) {
  const [loaded, setLoaded] = useState<{ tenantId: string | null; replies: CustomerReplies } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    readReplies(tenantId, controller.signal)
      .then((replies) => setLoaded({ tenantId, replies }))
      .catch(() => {});
    return () => controller.abort();
  }, [tenantId]);

  // The Inbox can switch tenants: an earlier tenant's state must not show.
  if (!loaded || loaded.tenantId !== tenantId) return null;
  const { replies } = loaded;
  const lines = bannerLines(replies.warnings);
  if (lines.length === 0) return null;
  const off = replies.warnings.includes("replies_off");
  return (
    <div className="mb-4" data-testid="customer-replies-banner">
      <Notice error>
        {lines[0]}{" "}
        {replies.can_manage
          ? <Link className="underline" href={servicesHref}>Go to Services</Link>
          : off && "Ask your admin to turn replies on."}
      </Notice>
    </div>
  );
}
