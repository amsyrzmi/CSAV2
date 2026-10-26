/** HR guidance path helper; the assistant dock answers HR guidance on the HR tab. */
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
