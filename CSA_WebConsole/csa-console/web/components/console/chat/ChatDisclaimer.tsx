/**
 * The one quiet line under the composer. It says where answers come from, so the assistant does
 * not look like it can do more than it can; it replaces the long list of limits.
 */
export function ChatHonestyLine() {
  return <p data-testid="chat-honesty-line" className="text-center text-xs text-[color:var(--fs-text-secondary)]">
    Answers come from your knowledge base and products.</p>;
}
