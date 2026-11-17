"use client";

import {
  bulkBody, bulkPath, errorCode, queueActionBody, queueActionPath,
  type BulkResponse, type BulkResult, type Conversation, type QueueActionName, type QueueActionResult,
  type QueueFields,
} from "@/lib/inbox-api";
import { actionErrorText, bulkSummary, queueActionNotice } from "@/lib/inbox-queue";
import type { InboxActions } from "./useInboxActions";

/** What `run` and `bulk` settle with. They never throw; the plain sentence is in `actions.error`. */
export type QueueRun = {
  ok: boolean;
  outcome?: QueueActionResult["outcome"];
  results?: BulkResult[];
  /** The server's code for a coded refusal (`held_reply_pending`, `customer_wrote`), or `busy`. */
  code?: string;
};

const NOT_SENT: QueueRun = { ok: false, code: "busy" };

/**
 * A request that may have been applied is kept (network failure, timeout, 5xx): Retry sends the same
 * request key again. A definitive refusal (4xx) is dropped. Either way the polled reads refresh.
 */
function failure(cause: unknown, status: number | null): string {
  const message = status === null || status >= 500
    ? "Something went wrong. Retry sends the same request again, so it cannot be done twice."
    : "That could not be done. Refreshed: look again before acting.";
  return actionErrorText(status, errorCode(cause), message);
}

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
        });
      },
      failure,
    });
    if (done.ok) return { ok: true, outcome: (done.result as QueueActionResult).outcome };
    return { ok: false, code: errorCode(done.cause) ?? undefined };
  }

  /** Many items. `seenAt` is the list's `as_of`. Per-item refusals come back in `results`, not as a failure. */
  async function bulk(
    action: QueueActionName, ids: string[], fields: QueueFields, seenAt: string,
  ): Promise<QueueRun> {
    if (actions.blocked) return NOT_SENT;
    const done = await actions.execute({
      path: bulkPath(query),
      payload: bulkBody(action, ids, fields, seenAt),
      notice: (result) => bulkSummary((result as BulkResponse).results).text,
      failure,
    });
    if (done.ok) return { ok: true, results: (done.result as BulkResponse).results };
    return { ok: false, code: errorCode(done.cause) ?? undefined };
  }

  return { run, bulk };
}
