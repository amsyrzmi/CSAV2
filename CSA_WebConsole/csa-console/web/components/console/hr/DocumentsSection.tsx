"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Modal } from "@/components/ui/kit";
import { ApiError } from "@/lib/api";
import { isDenied, shortWhen } from "@/lib/hr-pickers";
import { documentIssue, fileSizeText, RECORDS_COPY, recordsErrorText } from "@/lib/hr-records";
import { recordsClient, type DocumentKind, type PersonDocument } from "@/lib/hr-records-api";
import { action, field, secondary, Notice, Panel } from "../ui";

type Loaded = { items: PersonDocument[] } | { hidden: true } | { failed: true } | null;
/** The file chosen for upload. Its key is made with the choice and kept for retries of that exact upload. */
type Selection = { file: File; kind: DocumentKind; key: string };

const kindLabel = (kind: DocumentKind) => RECORDS_COPY.kinds.find(([id]) => id === kind)?.[1] ?? kind;
const failureOf = (cause: unknown) => cause instanceof ApiError ? { status: cause.status, code: cause.code } : null;
/** A definite answer (4xx) ends the attempt; anything else may have landed, so the retry keeps its key. */
const settled = (failure: { status: number } | null) => failure !== null && failure.status < 500;

/**
 * A person's documents: the list with Download for everyone who may see it, and Upload and Remove when
 * `canManage`. Upload keeps one Idempotency-Key per chosen file and kind, and Remove one per document,
 * so a retry after a lost response is the same request.
 */
export function DocumentsSection({ tenant, employeeId, canManage, title = "Documents" }: {
  tenant: string; employeeId: string; canManage: boolean; title?: string;
}) {
  const records = useMemo(() => recordsClient(tenant), [tenant]);
  const [loaded, setLoaded] = useState<Loaded>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [kind, setKind] = useState<DocumentKind>("contract");
  const [removing, setRemoving] = useState<PersonDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [message, setMessage] = useState("");
  const [inputKey, setInputKey] = useState(0);
  const removeKeys = useRef(new Map<string, string>());
  const inFlight = useRef(false);

  const fetchList = useCallback(async (): Promise<NonNullable<Loaded>> => {
    try { return { items: (await records.documents(employeeId)).items }; }
    catch (cause) { return isDenied(cause) ? { hidden: true } : { failed: true }; }
  }, [records, employeeId]);
  useEffect(() => {
    let active = true;
    void fetchList().then(result => { if (active) setLoaded(result); });
    return () => { active = false; };
  }, [fetchList]);

  /** A write succeeded: say so, then read the list again. A failed read says the write itself was saved. */
  async function afterWrite(done: string) {
    setMessage(done);
    const result = await fetchList();
    if ("items" in result) setLoaded(result);
    else setProblem("Saved, but the list could not refresh. Reload the page to see it.");
  }

  function choose(file: File | null, nextKind: DocumentKind) {
    setProblem(""); setMessage("");
    setSelection(file ? { file, kind: nextKind, key: crypto.randomUUID() } : null);
  }
  const selectionIssue = selection ? documentIssue(selection.file) : null;

  async function upload() {
    if (!selection || inFlight.current) return;
    const issue = documentIssue(selection.file);
    if (issue) { setProblem(issue); return; }
    inFlight.current = true; setBusy(true); setProblem(""); setMessage("");
    try {
      await records.upload(employeeId, selection.kind, selection.file, selection.key);
    } catch (cause) {
      const failure = failureOf(cause);
      setProblem(recordsErrorText(failure, "upload"));
      // A definite refusal ends this attempt; the next try is a new request.
      if (settled(failure)) setSelection({ ...selection, key: crypto.randomUUID() });
      inFlight.current = false; setBusy(false);
      return;
    }
    setSelection(null); setInputKey(count => count + 1);
    try { await afterWrite("Document uploaded."); } finally { inFlight.current = false; setBusy(false); }
  }

  async function remove(item: PersonDocument) {
    if (inFlight.current) return;
    const key = removeKeys.current.get(item.document_id) ?? crypto.randomUUID();
    removeKeys.current.set(item.document_id, key);
    inFlight.current = true; setBusy(true); setProblem(""); setMessage("");
    try {
      await records.removeDocument(item.document_id, key);
    } catch (cause) {
      const failure = failureOf(cause);
      setProblem(recordsErrorText(failure, "remove"));
      if (settled(failure)) removeKeys.current.delete(item.document_id);
      setRemoving(null); inFlight.current = false; setBusy(false);
      return;
    }
    removeKeys.current.delete(item.document_id); setRemoving(null);
    try { await afterWrite(`Removed ${item.file_name}.`); } finally { inFlight.current = false; setBusy(false); }
  }

  if (!loaded) return <Notice>Loading documents…</Notice>;
  if ("hidden" in loaded) return <Notice>Documents aren&apos;t available for this person.</Notice>;
  if ("failed" in loaded) return <Notice error>We could not load documents. Reload the page and try again.</Notice>;
  return <Panel title={title}>
    <div className="space-y-4" aria-busy={busy}>
      {message && <Notice>{message}</Notice>}
      {problem && <Notice error>{problem}</Notice>}
      {canManage && <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); void upload(); }}>
        <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
          <label className="block text-sm">Kind
            <select className={`${field} mt-1`} value={kind} onChange={event => {
              const next = event.target.value as DocumentKind;
              setKind(next);
              // The kind is part of the request: a new kind with a kept file is a new attempt.
              if (selection) choose(selection.file, next);
            }}>
              {RECORDS_COPY.kinds.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
          <label className="block text-sm">File
            <input key={inputKey} className={`${field} mt-1 h-auto py-2`} type="file"
              accept=".pdf,.jpg,.jpeg,.png,.docx" onChange={event => choose(event.target.files?.[0] ?? null, kind)} /></label>
          <button className={action} disabled={!selection || selectionIssue !== null}>Upload</button>
        </fieldset>
        {selectionIssue && <p className="w-full text-sm text-[var(--fs-danger)]" role="alert">{selectionIssue}</p>}
      </form>}
      {loaded.items.length === 0 ? <p className="text-sm text-ink-500">No documents yet.</p>
        : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead><tr>{["Kind", "File name", "Size", "Uploaded", "Actions"].map(label =>
            <th className="p-2" key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{loaded.items.map(item => <tr key={item.document_id} className="border-t border-ink-200">
            <td className="p-2">{kindLabel(item.kind)}</td>
            <td className="p-2 font-medium break-all">{item.file_name}</td>
            <td className="p-2">{fileSizeText(item.byte_size)}</td>
            <td className="p-2">{shortWhen(item.uploaded_at)}{item.uploaded_by_name && ` · ${item.uploaded_by_name}`}</td>
            <td className="p-2"><div className="flex flex-wrap gap-2">
              <a className={secondary} href={records.downloadHref(item.document_id)} download
                aria-label={`Download ${item.file_name}`}>Download</a>
              {canManage && <button type="button" className={secondary} disabled={busy} aria-label={`Remove ${item.file_name}`}
                onClick={() => setRemoving(item)}>Remove</button>}
            </div></td>
          </tr>)}</tbody>
        </table></div>}
    </div>
    <Modal isOpen={removing !== null} onClose={() => { if (!busy) setRemoving(null); }} title="Remove this document?">
      {removing && <div className="space-y-4 text-sm">
        <p>Remove {removing.file_name} from this person&apos;s documents?</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={action} disabled={busy} onClick={() => void remove(removing)}>Remove document</button>
          <button type="button" className={secondary} disabled={busy} onClick={() => setRemoving(null)}>Cancel</button>
        </div>
      </div>}
    </Modal>
  </Panel>;
}
