"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { usePublishContext } from "@/lib/page-context";
import { fileUrl, type TrainingItem, type TrainingViewMode } from "@/lib/training-api";
import { downloadName, itemFacts } from "@/lib/training-form";
import { itemHref, refusalCopy } from "@/lib/training-view";
import ItemDialog, { RemoveDialog } from "./training/ItemDialog";
import ItemList from "./training/ItemList";
import { PdfViewer } from "./training/PdfViewer";
import { muted, refusalText, useTrainingList, type ListRead } from "./training/shared";
import SummaryPanel from "./training/SummaryPanel";
import { field, Heading, Notice, secondary } from "./ui";

const ITEM_GONE = "This PDF is no longer in the library.";
const pageTitle = "page-title font-(family-name:--fs-font-heading) text-(length:--fs-text-page) font-semibold [overflow-wrap:anywhere]";
const backLink =
  "inline-flex min-h-10 items-center gap-1.5 rounded-[var(--fs-radius-control)] text-sm font-medium text-[color:var(--fs-primary)] underline-offset-2 hover:underline pointer-coarse:min-h-11 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

/** Where the two pages are, for this person and company. */
interface Scope {
  /** Null for a member (the session names the company); the chosen company for a platform admin. */
  tenantId: string | null;
  platform: boolean;
  tenant: string;
}

type Open = { kind: "add" } | { kind: "edit"; item: TrainingItem } | { kind: "remove"; item: TrainingItem } | null;

/**
 * The Training library: the list of the company's PDFs (no `itemId`), or one PDF with its viewer
 * and summary. Everyone in the company reads; managers (`can_manage` on the list: admins and the
 * HR owner) also add, edit and remove. A platform admin's company lives in the URL
 * (`?client_id=`) and nowhere else, as on the other tabs.
 */
export function TrainingWorkspace({ me, itemId }: { me: Me; itemId?: string }) {
  usePublishContext({ tab: "training_library", section: itemId ? "item" : "library" });
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const platform = me.role === "platform_admin";
  const [clients, setClients] = useState<Client[] | null>(null);
  const [error, setError] = useState("");
  const selected = search.get("client_id") ?? "";
  const tenant = (platform ? selected : me.active_client_id) ?? "";
  const scope: Scope = { tenantId: platform ? tenant || null : null, platform, tenant };

  useEffect(() => {
    // The company list is only for the picker on the list page.
    if (!platform || itemId) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform, itemId]);

  const chooseTenant = (value: string) => {
    router.replace(value ? `${pathname}?client_id=${encodeURIComponent(value)}` : pathname, { scroll: false });
  };

  if (itemId) {
    if (!tenant)
      return (
        <>
          <Heading title="Training library" description="Choose a company to open its training PDFs." />
          <Notice>
            Choose a company first.{" "}
            <Link className="underline" href={itemHref(null, platform, null)}>Go to the library</Link>
          </Notice>
        </>
      );
    return <ItemPage key={`${tenant}/${itemId}`} scope={scope} itemId={itemId} />;
  }

  return (
    <>
      <Heading
        title="Training library"
        description="Your company's training PDFs, to read as slides or as a document."
      />
      {error && <div className="mb-5"><Notice error>{error}</Notice></div>}
      {platform && (
        <label className="mb-5 block text-sm">Company
          <select className={`${field} mt-1`} data-testid="training-tenant" value={selected}
            onChange={(event) => chooseTenant(event.target.value)}>
            <option value="">{clients ? "Choose a company" : "Loading companies…"}</option>
            {(clients ?? []).map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </select>
        </label>
      )}
      {!tenant ? (
        <Notice>Choose a company to see its training library.</Notice>
      ) : (
        <LibraryPage key={tenant} scope={scope} />
      )}
    </>
  );
}

/** The list read's own states: loading, a failure with a retry, or the list. */
function ListGate({ list, children }: { list: ListRead; children: (data: NonNullable<ListRead["data"]>) => ReactNode }) {
  if (list.data) {
    return (
      <>
        {list.error !== null && <div className="mb-4"><Notice error testId="training-list-error">{refusalCopy(list.error)}</Notice></div>}
        {children(list.data)}
      </>
    );
  }
  if (list.error !== null) {
    return (
      <div className="space-y-3">
        <Notice error testId="training-list-error">{refusalCopy(list.error)}</Notice>
        <button type="button" className={secondary} onClick={list.reload}>Try again</button>
      </div>
    );
  }
  return <p role="status" aria-busy="true" className={muted} data-testid="training-loading">Loading the library…</p>;
}

function LibraryPage({ scope }: { scope: Scope }) {
  const list = useTrainingList(scope.tenantId);
  const [open, setOpen] = useState<Open>(null);
  const [message, setMessage] = useState("");
  const hrefFor = (id: string) => itemHref(id, scope.platform, scope.tenantId);

  return (
    <ListGate list={list}>
      {(data) => (
        <>
          {message && <div className="mb-4"><Notice testId="training-message">{message}</Notice></div>}
          <ItemList
            list={data}
            hrefFor={hrefFor}
            onAdd={() => { setMessage(""); setOpen({ kind: "add" }); }}
            onEdit={(item) => { setMessage(""); setOpen({ kind: "edit", item }); }}
            onRemove={(item) => { setMessage(""); setOpen({ kind: "remove", item }); }}
          />
          {open && open.kind !== "remove" && (
            <ItemDialog
              tenantId={scope.tenantId}
              item={open.kind === "edit" ? open.item : null}
              categories={data.categories}
              onClose={() => setOpen(null)}
              onChanged={list.reload}
              onSaved={(saved, added) => {
                setOpen(null);
                setMessage(added
                  ? `Added ${saved.title}. Everyone in your company can open it now.`
                  : `Saved the details of ${saved.title}.`);
                list.reload();
              }}
            />
          )}
          {open?.kind === "remove" && (
            <RemoveDialog
              tenantId={scope.tenantId}
              item={open.item}
              onClose={() => setOpen(null)}
              onRemoved={(removedNow) => {
                setOpen(null);
                setMessage(removedNow ? `Removed ${open.item.title} from the library.` : ITEM_GONE);
                list.reload();
              }}
            />
          )}
        </>
      )}
    </ListGate>
  );
}

function ItemPage({ scope, itemId }: { scope: Scope; itemId: string }) {
  const list = useTrainingList(scope.tenantId);
  const router = useRouter();
  const [mode, setMode] = useState<TrainingViewMode | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [message, setMessage] = useState("");
  const listHref = itemHref(null, scope.platform, scope.tenantId);
  const back = (
    <Link href={listHref} className={backLink} data-testid="training-back">
      <ArrowLeft aria-hidden="true" size={16} />
      Back to the library
    </Link>
  );
  const item = list.data?.items.find((row) => row.id === itemId) ?? null;

  if (!list.data || !item) {
    return (
      <>
        <div className="mb-3">{back}</div>
        <h1 className={`${pageTitle} mb-5`}>Training library</h1>
        <ListGate list={list}>
          {() => (
            <Notice testId="training-gone">
              {ITEM_GONE}{" "}
              <Link className="underline" href={listHref}>See the library</Link>
            </Notice>
          )}
        </ListGate>
      </>
    );
  }

  const manage = list.data.can_manage;
  return (
    <div className="min-w-0 space-y-5">
      <div>
        <div className="mb-2">{back}</div>
        <h1 className={pageTitle} data-testid="training-title-heading">{item.title}</h1>
        {item.description && <p className="page-description mt-2 whitespace-pre-line [overflow-wrap:anywhere]">{item.description}</p>}
        <ul className={`mt-2 flex flex-wrap gap-x-3 gap-y-1 ${muted}`} data-testid="training-facts">
          {itemFacts(item).map((fact, index) => <li key={index}>{fact}</li>)}
        </ul>
      </div>
      {message && <Notice testId="training-message">{message}</Notice>}
      {list.error !== null && <Notice error testId="training-list-error">{refusalCopy(list.error)}</Notice>}
      <div className="flex flex-wrap gap-2">
        <DownloadButton tenantId={scope.tenantId} item={item} />
        {manage && (
          <>
            <button type="button" className={secondary} data-testid="training-edit"
              onClick={() => { setMessage(""); setOpen({ kind: "edit", item }); }}>
              Edit details
            </button>
            <button type="button" className={secondary} data-testid="training-remove"
              onClick={() => { setMessage(""); setOpen({ kind: "remove", item }); }}>
              Remove PDF
            </button>
          </>
        )}
      </div>
      <PdfViewer tenantId={scope.tenantId} itemId={item.id} title={item.title} mode={mode ?? item.view_mode} onMode={setMode} />
      <SummaryPanel key={item.id} tenantId={scope.tenantId} item={item} canManage={manage} />
      {open?.kind === "edit" && (
        <ItemDialog
          tenantId={scope.tenantId}
          item={open.item}
          categories={list.data.categories}
          onClose={() => setOpen(null)}
          onChanged={list.reload}
          onSaved={(saved) => {
            setOpen(null);
            setMessage(`Saved the details of ${saved.title}.`);
            list.reload();
          }}
        />
      )}
      {open?.kind === "remove" && (
        <RemoveDialog
          tenantId={scope.tenantId}
          item={open.item}
          onClose={() => setOpen(null)}
          onRemoved={(removedNow) => {
            setOpen(null);
            // Already gone: read the list again, and the page says the PDF is no longer there.
            if (removedNow) router.push(listHref);
            else list.reload();
          }}
        />
      )}
    </div>
  );
}

/**
 * "Download PDF": the same file route the viewer reads, saved under the item's title. The bytes
 * go through a short-lived blob URL; nothing about the file is logged.
 */
function DownloadButton({ tenantId, item }: { tenantId: string | null; item: TrainingItem }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const running = useRef(false);

  async function download() {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setProblem("");
    try {
      const response = await fetch(fileUrl(tenantId, item.id), { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        const code = typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string"
          ? body.detail : "";
        setProblem(refusalCopy(code));
        return;
      }
      // Anything but a PDF (a sign-in page after a lapsed session, say) is a failed download.
      if (!(response.headers.get("Content-Type") ?? "").startsWith("application/pdf")) {
        setProblem(refusalCopy(""));
        return;
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = downloadName(item.title);
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (cause) {
      setProblem(refusalText(cause));
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className={secondary} disabled={busy} aria-busy={busy} data-testid="training-download"
        onClick={() => void download()}>
        {busy ? "Downloading…" : "Download PDF"}
      </button>
      {problem && <div className="basis-full"><Notice error testId="training-download-error">{problem}</Notice></div>}
    </>
  );
}
