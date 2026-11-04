import { ApiError } from "@/lib/api";
import { PEOPLE_COPY, peopleRefusalText } from "@/lib/hr-people";

/**
 * The plain sentence for a failed people write: the refusal's own sentence for a server detail code,
 * else the generic one. A raw code never reaches the screen.
 */
export function peopleWriteError(cause: unknown): string {
  return cause instanceof ApiError ? peopleRefusalText(cause.code) : PEOPLE_COPY.refusalFallback;
}
