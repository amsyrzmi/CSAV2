import type { Domain } from "./hr-api";

/**
 * What the HR requests panel may offer, from the viewer's `domain.action` workflow grants
 * (`HrContext.workflows`): Prepare for domains with `<domain>.prepare`, approve and reject for
 * domains with `<domain>.decide`. The server still decides each item and may refuse.
 */
export function requestActions(workflows: readonly string[], domains: readonly Domain[]) {
  const grants = new Set(workflows);
  const decide = new Set(domains.filter((domain) => grants.has(`${domain}.decide`)));
  return {
    prepare: domains.filter((domain) => grants.has(`${domain}.prepare`)),
    decides: (domain: Domain) => decide.has(domain),
  };
}
