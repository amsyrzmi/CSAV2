"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  channelsText,
  contactLabel,
  linkBody,
  linkChoiceProblem,
  linkReasonText,
  reviewReasonSentence,
  skippedSentence,
  unlinkNote,
} from "@/lib/contact-links";
import {
  formatDay,
  linkContacts,
  listLinks,
  listRecentLinks,
  listReviews,
  resolveReview,
  searchCustomers,
  unlinkContacts,
  type CustomerRef,
  type LinkedContact,
  type LinkOut,
  type ReviewOut,
  type ReviewResolution,
} from "@/lib/subscriptions-api";
import { action, field, Notice, secondary } from "./ui";

function message(cause: unknown) {
  return cause instanceof Error ? cause.message : "The request failed. Try again.";
}

/** Codes that mean the list on screen is out of date: read it again. */
const STALE = new Set(["review_not_open", "review_not_found", "link_not_active", "link_not_found"]);

function Who({ contact, mark }: { contact: LinkedContact; mark?: string }) {
  return (
    <div className="min-w-0" data-testid="link-contact">
      <p className="font-medium">
        {contactLabel(contact)}
        {mark && <span className="ml-2 text-xs font-normal text-ink-500">{mark}</span>}
      </p>
      {contact.phone && contact.full_name && <p className="text-ink-600">{contact.phone}</p>}
      <p className="text-xs text-ink-500" data-testid="link-channels">{channelsText(contact.channels)}</p>
    </div>
  );
}

/**
 * One list of links read with `read` (a stable callback: a new one, or a new `outer` revision, reads
 * again), and Unlink on any of them: what both link lists share. `onUnlinked` runs after an unlink.
 */
function useLinks(
  read: () => Promise<LinkOut[]>, tenantId: string | null, onUnlinked: () => void, outer = 0,
) {
  const [links, setLinks] = useState<LinkOut[] | null>(null);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    read()
      .then((rows) => { if (live) { setLinks(rows); setError(""); } })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [read, revision, outer]);
  const reread = () => setRevision((value) => value + 1);
  async function unlink(link: LinkOut) {
    setBusy(link.id);
    setError("");
    setDone("");
    try {
      const result = await unlinkContacts(tenantId, link.id);
      setDone(skippedSentence(result.skipped, link) ?? "Unlinked. The two contacts are separate again.");
      reread();
      onUnlinked();
    } catch (cause) {
      setError(message(cause));
      if (cause instanceof ApiError && STALE.has(cause.code)) reread();
    } finally {
      setBusy(null);
    }
  }
  return { links, error, done, setDone, busy, unlink, reread };
}

/** One link: both contacts, how and when they were linked, and Unlink while the server allows it. */
function LinkRow({ link, contactId, writer, busy, now, timeZone, onUnlink }: {
  link: LinkOut; contactId?: string; writer: boolean; busy: string | null; now: Date;
  timeZone: string | undefined; onUnlink: (link: LinkOut) => void;
}) {
  const note = unlinkNote(link, now, timeZone);
  const mark = (id: string) => (id === contactId ? "This customer" : undefined);
  return (
    <li data-testid="link-row"
      className={`rounded-md border border-ink-200 p-3 ${link.unlinked_at ? "opacity-70" : ""}`}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Who contact={link.survivor} mark={mark(link.survivor.id)} />
        <Who contact={link.loser} mark={mark(link.loser.id)} />
      </div>
      <p className="mt-2 text-xs text-ink-500" data-testid="link-meta">
        {linkReasonText(link.reason)} · {formatDay(link.created_at, timeZone)}
      </p>
      {note ? <p className="mt-2 text-ink-600" data-testid="link-note">{note}</p>
        : writer && (
          <div className="mt-2">
            <button type="button" className={secondary} data-testid="link-unlink"
              disabled={busy !== null} onClick={() => onUnlink(link)}>
              {busy === link.id ? "Unlinking…" : "Unlink"}</button>
          </div>
        )}
    </li>
  );
}

/**
 * The contacts linked to one customer, with Unlink while the server allows it, and a way for staff
 * to link another contact. Read-only members see the list and none of the controls.
 */
export function LinkedContacts({ tenantId, contactId, writer, timeZone, onChanged }: {
  tenantId: string | null; contactId: string; writer: boolean; timeZone: string | undefined;
  onChanged: () => void;
}) {
  const read = useCallback(() => listLinks(tenantId, contactId), [tenantId, contactId]);
  const { links, error, done, setDone, busy, unlink, reread } = useLinks(read, tenantId, onChanged);
  const [picking, setPicking] = useState(false);
  const now = new Date();
  return (
    <section aria-label="Linked contacts" data-testid="linked-contacts" className="mt-5">
      <h3 className="mb-2 font-semibold">Linked contacts</h3>
      <p className="mb-3 text-xs text-ink-600">
        When one person reaches you on more than one channel, their contacts can be linked so their chats and
        subscriptions are kept together.
      </p>
      {done && <div className="mb-3"><Notice testId="link-done">{done}</Notice></div>}
      {error && <div className="mb-3"><Notice error>{error}</Notice></div>}
      {links === null ? (!error && <Notice>Loading linked contacts…</Notice>) : links.length === 0 ? (
        <p className="text-sm text-ink-500" data-testid="link-empty">This customer is not linked to any other contact.</p>
      ) : (
        <ul className="space-y-3 text-sm" data-testid="link-list">
          {links.map((link) => (
            <LinkRow key={link.id} link={link} contactId={contactId} writer={writer} busy={busy} now={now}
              timeZone={timeZone} onUnlink={(row) => void unlink(row)} />
          ))}
        </ul>
      )}
      {writer && links !== null && (picking ? (
        <LinkPicker tenantId={tenantId} contactId={contactId} links={links}
          onCancel={() => setPicking(false)}
          onLinked={(text) => { setPicking(false); setDone(text); reread(); onChanged(); }} />
      ) : (
        <div className="mt-3">
          <button type="button" className={secondary} data-testid="link-another"
            onClick={() => { setPicking(true); setDone(""); }}>Link another contact</button>
        </div>
      ))}
    </section>
  );
}

/** Find a contact with the customer search, then confirm before anything is linked. */
function LinkPicker({ tenantId, contactId, links, onCancel, onLinked }: {
  tenantId: string | null; contactId: string; links: LinkOut[]; onCancel: () => void;
  onLinked: (text: string) => void;
}) {
  const [term, setTerm] = useState("");
  const [found, setFound] = useState<CustomerRef[] | null>(null);
  const [chosen, setChosen] = useState<CustomerRef | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const problem = chosen ? linkChoiceProblem(contactId, chosen, links) : null;
  async function find() {
    setError("");
    try {
      setFound(await searchCustomers(tenantId, term));
    } catch (cause) {
      setError(message(cause));
    }
  }
  async function confirm() {
    if (!chosen || problem) return;
    setBusy(true);
    setError("");
    try {
      const made = await linkContacts(tenantId, linkBody(contactId, chosen.id));
      onLinked(`Linked ${contactLabel(made.survivor)} and ${contactLabel(made.loser)}. Their chats and subscriptions are now kept together.`);
    } catch (cause) {
      setError(message(cause));
      setBusy(false);
    }
  }
  const detail = (who: CustomerRef) => (who.full_name && (who.phone || who.email) ? ` · ${who.phone || who.email}` : "");
  return (
    <div className="mt-3 space-y-3 rounded-md border border-ink-200 p-4" data-testid="link-picker">
      {chosen ? (
        <div className="space-y-3 text-sm" data-testid="link-confirm">
          <p>
            Link <span className="font-medium">{customerLabel(chosen)}</span> to this customer? Their chats and
            subscriptions will be kept together. You can undo this for 30 days.
          </p>
          {problem && <Notice warning>{problem}</Notice>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={action} data-testid="link-confirm-yes" disabled={busy || problem !== null}
              onClick={() => void confirm()}>{busy ? "Linking…" : "Link contacts"}</button>
            <button type="button" className={secondary} disabled={busy}
              onClick={() => { setChosen(null); setError(""); }}>Choose another contact</button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <label className="block text-sm" htmlFor="link-search">Contact to link</label>
          <div className="flex flex-wrap gap-2">
            <input id="link-search" data-testid="link-search" className={`${field} min-w-0 flex-1`} type="search"
              maxLength={100} placeholder="Name, phone or email" value={term}
              onChange={(event) => setTerm(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void find(); } }} />
            <button type="button" className={secondary} data-testid="link-find" onClick={() => void find()}>
              Find contacts</button>
          </div>
          {found && (found.length === 0
            ? <p className="text-sm text-ink-600">No contacts match. Try another name, phone or email.</p>
            : (
              <ul className="divide-y divide-ink-100 rounded-md border border-ink-200 text-sm">
                {found.map((who) => (
                  <li key={who.id}>
                    <button type="button" data-testid="link-option" className="w-full px-3 py-2 text-left hover:bg-ink-50"
                      onClick={() => { setChosen(who); setError(""); }}>
                      <span className="font-medium">{customerLabel(who)}</span>
                      <span className="text-ink-500">{detail(who)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ))}
          <div><button type="button" className={secondary} onClick={onCancel}>Cancel</button></div>
        </div>
      )}
      {error && <Notice error>{error}</Notice>}
    </div>
  );
}

function customerLabel(who: CustomerRef) {
  return who.full_name || who.phone || who.email || "Unnamed customer";
}

/**
 * Every link of the company, newest first, whoever the contacts are: the place to see and undo a
 * link made by a verified phone, also for customers without a subscription. Read-only members see
 * the list and no Unlink.
 */
export function RecentLinks({ tenantId, writer, timeZone, revision, onChanged }: {
  tenantId: string | null; writer: boolean; timeZone: string | undefined; revision: number;
  onChanged: () => void;
}) {
  // `revision` changes when the tab decides a review or a sign-up: the list is read again.
  const read = useCallback(() => listRecentLinks(tenantId), [tenantId]);
  const { links, error, done, busy, unlink } = useLinks(read, tenantId, onChanged, revision);
  const now = new Date();
  return (
    <section aria-label="Recent links" data-testid="recent-links">
      {done && <div className="mb-3"><Notice testId="link-done">{done}</Notice></div>}
      {error && <div className="mb-3"><Notice error>{error}</Notice></div>}
      {links === null ? (!error && <Notice>Loading links…</Notice>) : links.length === 0 ? (
        <Notice>No contacts have been linked yet. When one person&apos;s contacts are linked, by a verified phone
          number or by staff, the link appears here and can be undone for 30 days.</Notice>
      ) : (
        <ul className="space-y-3 text-sm" data-testid="recent-link-list">
          {links.map((link) => (
            <LinkRow key={link.id} link={link} writer={writer} busy={busy} now={now} timeZone={timeZone}
              onUnlink={(row) => void unlink(row)} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Pairs of contacts the system could not link by itself: staff link them or say they differ. */
export function NeedsReview({ tenantId, writer, timeZone, revision, onResolved }: {
  tenantId: string | null; writer: boolean; timeZone: string | undefined; revision: number;
  onResolved: (text: string) => void;
}) {
  const [rows, setRows] = useState<ReviewOut[] | null>(null);
  const [error, setError] = useState("");
  const [local, setLocal] = useState(0);
  useEffect(() => {
    let live = true;
    listReviews(tenantId, "open")
      .then((items) => { if (live) { setRows(items); setError(""); } })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [tenantId, revision, local]);
  if (rows === null) return error ? <Notice error>{error}</Notice> : <Notice>Loading reviews…</Notice>;
  return (
    <>
      {error && <div className="mb-4"><Notice error>{error}</Notice></div>}
      {rows.length === 0 ? (
        <Notice>Nothing needs review. When two contacts might be the same person but could not be linked
          automatically, they will appear here.</Notice>
      ) : (
        <ul className="divide-y divide-ink-100" data-testid="reviews-open">
          {rows.map((row) => (
            <ReviewRow key={row.id} row={row} tenantId={tenantId} writer={writer} timeZone={timeZone}
              onResolved={(text) => { onResolved(text); setLocal((value) => value + 1); }}
              onStale={() => setLocal((value) => value + 1)} />
          ))}
        </ul>
      )}
    </>
  );
}

function ReviewRow({ row, tenantId, writer, timeZone, onResolved, onStale }: {
  row: ReviewOut; tenantId: string | null; writer: boolean; timeZone: string | undefined;
  onResolved: (text: string) => void; onStale: () => void;
}) {
  const [busy, setBusy] = useState<ReviewResolution | null>(null);
  const [error, setError] = useState("");
  const names = `${contactLabel(row.contact_a)} and ${contactLabel(row.contact_b)}`;
  async function resolve(resolution: ReviewResolution) {
    setBusy(resolution);
    setError("");
    try {
      await resolveReview(tenantId, row.id, resolution);
      onResolved(resolution === "linked"
        ? `Linked ${names}. Their chats and subscriptions are now kept together.`
        : `Closed. ${names} stay separate contacts.`);
    } catch (cause) {
      setError(message(cause));
      setBusy(null);
      if (cause instanceof ApiError && STALE.has(cause.code)) onStale();
    }
  }
  return (
    <li className="py-3 text-sm" data-testid="review-open">
      <div className="grid gap-3 sm:grid-cols-2">
        <Who contact={row.contact_a} />
        <Who contact={row.contact_b} />
      </div>
      <p className="mt-2" data-testid="review-reason">{reviewReasonSentence(row.reason)}</p>
      <p className="text-xs text-ink-500">Found {formatDay(row.created_at, timeZone)}</p>
      {writer && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={action} disabled={busy !== null} data-testid="review-link"
            onClick={() => void resolve("linked")}>{busy === "linked" ? "Linking…" : "Link them"}</button>
          <button type="button" className={secondary} disabled={busy !== null} data-testid="review-dismiss"
            onClick={() => void resolve("dismissed")}>{busy === "dismissed" ? "Closing…" : "Not the same person"}</button>
        </div>
      )}
      {error && <div className="mt-3"><Notice error>{error}</Notice></div>}
    </li>
  );
}
