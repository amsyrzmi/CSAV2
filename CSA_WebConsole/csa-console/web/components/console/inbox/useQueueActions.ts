"use client";

import { ApiError } from "@/lib/api";
import {
  bulkBody, bulkPath, errorCode, queueActionBody, queueActionPath,
  type BulkResponse, type BulkResult, type Conversation, type QueueActionName, type QueueActionResult,
  type QueueFields,
} from "@/lib/inbox-api";
import { bulkSummary, queueActionNotice, queueFailureIsFinal, queueFailureText } from "@/lib/inbox-queue";
import type { InboxActions } from "./useInboxActions";

/**
 * What `run` and `bulk` settle with. They never throw. After a request the plain sentence is in
 * `actions.notice` (done) or `actions.error` (refused or uncertain). While `actions.blocked` (a request
 * in flight, or an uncertain one kept for Retry) nothing is sent and nothing is said (`code: "busy"`),
 * so callers disable their queue controls then.
 */
export type QueueRun = {
  ok: boolean;
  outcome?: QueueActionResult["outcome"];
  results?: BulkResult[];
  /** The server's code for a coded refusal (`held_reply_pending`, `customer_wrote`), or `busy`. */
  code?: string;
};

const NOT_SENT: QueueRun = { ok: false, code: "busy" };

/** The server's plain-text detail of a failed call (`ApiError.code`), or null without an answer. */
const detailOf = (cause: unknown) => (cause instanceof ApiError ? cause.code : null);

/**
 * A request that may have been applied is kept (network failure, timeout, 5xx): Retry sends the same
 * request key again, and the sentence says so. A settled refusal (4xx, and assign's member-directory
 * 503, which writes nothing) is dropped, so it never holds the Inbox's controls. Either way the
 * polled reads refresh.
 */
const failureFor = (action: QueueActionName) => ({
  failure: (cause: unknown, status: number | null): string =>
    queueFailureText(action, status, errorCode(cause), detailOf(cause)),
  definitive: (status: number | null, cause: unknown): boolean =>
    queueFailureIsFinal(action, status, detailOf(cause)),
});

/**
 * The queue's write paths over the Inbox's shared plumbing (`actions`): the busy flag, the notice and
 * error line, the pending request and the revision all come from `useInboxActions`.
 */
export function useQueueActions(query: string, actions: InboxActions) {
  /** One item. `fields` carries the action's value; `assigneeName` is for the notice only. */
  async function run(action: QueueActionName, item: Conversation, fields: QueueFields = {}): Promise<QueueRun> {
    // A kept request still owns the request key: a second action would replace it.
    if (actions.blocked) return NOT_SENT;
    const done = await actions.execute({
      path: queueActionPath(`/console/v1/inbox/conversations/${item.id}`, action, query),
      payload: queueActionBody(action, item, fields),
      notice: (result) => {
        const { outcome, conversation } = result as QueueActionResult;
        return queueActionNotice(action, outcome, {
          until: conversation?.snoozed_until ?? fields.until, priority: fields.priority, topic: fields.topic,
          assigneeName: fields.assigneeName,
        }, Date.now());
      },
      ...failureFor(action),
    });
    if (done.ok) return { ok: true, outcome: (done.result as QueueActionResult).outcome };
    return { ok: false, code: errorCode(done.cause) ?? undefined };
  }

  /**
   * Many items. `seenAt` is the list's `as_of`. Per-item refusals come back in `results`, not as a
   * failure. `onResults` gets them on success, also when an uncertain request succeeds on Retry
   * (which settles outside this call), so callers act on results there rather than on the return.
   */
  async function bulk(
    action: QueueActionName, ids: string[], fields: QueueFields, seenAt: string,
    onResults?: (results: BulkResult[]) => void,
  ): Promise<QueueRun> {
    if (actions.blocked) return NOT_SENT;
    const done = await actions.execute({
      path: bulkPath(query),
      payload: bulkBody(action, ids, fields, seenAt),
      notice: (result) => bulkSummary((result as BulkResponse).results).text,
      onResult: onResults ? (result) => onResults((result as BulkResponse).results) : undefined,
      ...failureFor(action),
    });
    if (done.ok) return { ok: true, results: (done.result as BulkResponse).results };
    return { ok: false, code: errorCode(done.cause) ?? undefined };
  }

  return { run, bulk };
}
