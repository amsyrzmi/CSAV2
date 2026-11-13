/** One mapping from stable service ids and console capability names to avatar files. */
export const AGENT_IDS = [
  "support",
  "knowledge",
  "orders",
  "tickets",
  "ecommerce",
  "invoices",
  "assistant",
  "hr",
  "email",
  "subscriptions",
  "bookings",
  "einvoicing",
] as const;

export type AgentId = (typeof AGENT_IDS)[number];

export const AGENT_TITLES: Record<AgentId, string> = {
  support: "Customer support assistant",
  knowledge: "Business knowledge agent",
  orders: "Order management assistant",
  tickets: "Support cases & hand-over assistant",
  ecommerce: "E-commerce operations assistant",
  invoices: "Invoice lookup assistant",
  assistant: "Workspace assistant",
  hr: "HR assistant",
  email: "Email assistant",
  subscriptions: "Subscriptions assistant",
  bookings: "Bookings assistant",
  einvoicing: "E-invoicing assistant",
};

const CAPABILITY_AVATARS = new Map<string, AgentId>([
  ["kb_agent", "knowledge"],
  ["ticket_agent", "tickets"],
  ["escalation_agent", "tickets"],
  ["order_agent", "orders"],
  ["order_approval", "orders"],
  ["invoice_agent", "invoices"],
  ["ecommerce_agent", "ecommerce"],
  ["subscription_agent", "subscriptions"],
  ["payment_agent", "subscriptions"],
  ["booking_agent", "bookings"],
  ["lhdn_agent", "einvoicing"],
]);

/** The avatar for a console capability name or a service id; null when there is none (an unknown name). */
export function avatarForAgent(name: string): AgentId | null {
  const capability = CAPABILITY_AVATARS.get(name);
  if (capability) return capability;
  return (AGENT_IDS as readonly string[]).includes(name) ? (name as AgentId) : null;
}

const PACKAGE_AVATARS = new Map<string, AgentId>([
  ["customer_support", "support"],
  ["orders_products", "orders"],
  ["subscriptions", "subscriptions"],
  ["bookings", "bookings"],
  ["marketplaces", "ecommerce"],
  ["hr", "hr"],
  ["staff_email", "email"],
  ["e_invoicing", "einvoicing"],
]);

/** The avatar for a package key; null when the key is unknown. */
export function avatarForPackage(key: string): AgentId | null {
  return PACKAGE_AVATARS.get(key) ?? null;
}
