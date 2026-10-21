export function workspaceEndpoint(tenantId, threadId = null, turns = false) {
  const threadPath = threadId ? `/${encodeURIComponent(threadId)}` : "";
  const suffix = turns ? "/turns" : "";
  const query = new URLSearchParams();
  if (tenantId) query.set("client_id", tenantId);
  const search = query.size ? `?${query.toString()}` : "";
  return `/console/v1/workspace/conversations${threadPath}${suffix}${search}`;
}

const guidance = Object.freeze({
  workspace_home_help:
    "Use this internal workspace for staff guidance and authorized local business reads. Customer sandbox conversations are separate.",
  business_orders_help:
    "Business order views read tenant-scoped local records only. They do not query a marketplace or change an order.",
  business_order_help:
    "This order lookup reads one tenant-scoped local record. Confirm its source and local update time before relying on it.",
});

export function guidanceText(key) {
  return guidance[key] ?? "No guidance is available for this page.";
}
