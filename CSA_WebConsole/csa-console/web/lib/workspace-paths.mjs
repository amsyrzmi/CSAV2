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

const hrGuides = Object.freeze({
  "directory.read": "Browse the employee directory. Only granted fields and eligible employees are returned.",
  "training.read": "View training requests within your granted scope.",
  "training.draft": "Prepare a training draft in People & HR. Saving a draft does not submit it.",
  "training.submit": "Submit an eligible saved training draft from People & HR.",
  "training.withdraw": "Withdraw an eligible training request from People & HR.",
  "training.decide": "Review an eligible training request in People & HR. Prepare the decision, then explicitly confirm it; preparation alone changes nothing.",
});

export function hrGuidanceText(action) {
  if (!Object.hasOwn(hrGuides, action)) throw new Error("Unknown HR guidance action");
  return hrGuides[action];
}

export function hrGuidancePath(action, tenantId, platform) {
  hrGuidanceText(action);
  const section = action === "directory.read" ? "directory" : "training";
  const query = new URLSearchParams({ section });
  if (platform) query.set("client_id", tenantId);
  return `${platform ? "/hr" : "/portal/hr"}?${query}`;
}
