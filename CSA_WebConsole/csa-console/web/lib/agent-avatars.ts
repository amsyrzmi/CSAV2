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
] as const;

export type AgentId = (typeof AGENT_IDS)[number];

export const AGENT_TITLES: Record<AgentId, string> = {
  support: "Customer support agent",
  knowledge: "Knowledge base agent",
  orders: "Order management agent",
  tickets: "Ticketing & handoff agent",
  ecommerce: "E-commerce operations agent",
  invoices: "Invoice lookup agent",
  assistant: "Workspace assistant",
  hr: "HR assistant",
  email: "Email assistant",
  subscriptions: "Subscriptions agent",
  bookings: "Bookings agent",
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
  ["booking_agent", "bookings"],
]);

/** The avatar for a console capability name or a service id; null when there is none (for example lhdn_agent). */
export function avatarForAgent(name: string): AgentId | null {
  const capability = CAPABILITY_AVATARS.get(name);
  if (capability) return capability;
  return (AGENT_IDS as readonly string[]).includes(name) ? (name as AgentId) : null;
}
