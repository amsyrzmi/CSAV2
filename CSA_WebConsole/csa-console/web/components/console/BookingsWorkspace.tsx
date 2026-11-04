"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { bookingsPages, type BookingsSection } from "@/lib/navigation";
import { usePublishContext } from "@/lib/page-context";
import { todayInZone } from "@/lib/bookings-view";
import BookingsCalendar from "./bookings/BookingsCalendar";
import BookingTypes from "./bookings/BookingTypes";
import PeopleAndRooms from "./bookings/PeopleAndRooms";
import type { BookingsScope, ZoneState } from "./bookings/parts";
import { SectionNav, useSectionArrivalFocus } from "./kit";
import { field, Heading, Notice } from "./ui";

type ZoneRead = { zone: string } | "failed" | null;

/** A platform admin's company zone comes from the company list: loading until it arrives. */
function chosenZone(clients: Client[] | null, selected: string, error: string): ZoneRead {
  if (error) return "failed";
  if (!clients) return null;
  const found = clients.find((client) => client.id === selected);
  return found ? { zone: found.timezone } : "failed";
}

/** A zone counts only if the browser can read times in it; anything else is a failed read. */
function readZone(read: ZoneRead): { zone: string | null; zoneState: ZoneState } {
  if (read === null) return { zone: null, zoneState: "loading" };
  if (read === "failed" || !read.zone || todayInZone(read.zone) === null) return { zone: null, zoneState: "failed" };
  return { zone: read.zone, zoneState: "loaded" };
}

/**
 * The Bookings tab: a section bar over its pages. Everyone in the company can read the setup; only
 * an Admin (or a platform admin who chose the company) sees the controls that change it. A platform
 * admin's company lives in the URL (`?client_id=`) and nowhere else, so the page always agrees with
 * the address and a rail link without one asks for a company.
 */
export function BookingsWorkspace({ me, section = "calendar" }: { me: Me; section?: BookingsSection }) {
  usePublishContext({ tab: "bookings", section });
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const platform = me.role === "platform_admin";
  const [clients, setClients] = useState<Client[] | null>(null);
  const [error, setError] = useState("");
  // Null while the member's profile is being read, "failed" when it could not be.
  const [profileRead, setProfileRead] = useState<ZoneRead>(null);
  const pageHeading = useRef<HTMLHeadingElement>(null);
  useSectionArrivalFocus(pageHeading);
  const selected = search.get("client_id") ?? "";
  const tenant = platform ? selected : me.active_client_id;

  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform]);

  useEffect(() => {
    // A member's own company: its time zone is on the profile.
    if (platform || !tenant) return;
    const controller = new AbortController();
    api<Client>("/console/v1/profile", { signal: controller.signal })
      .then((client) => setProfileRead({ zone: client.timezone }))
      .catch(() => { if (!controller.signal.aborted) setProfileRead("failed"); });
    return () => controller.abort();
  }, [platform, tenant]);

  const { zone, zoneState } = readZone(platform ? chosenZone(clients, selected, error) : profileRead);
  const tenantQuery = platform && tenant ? `client_id=${encodeURIComponent(tenant)}` : "";
  const pages = bookingsPages(platform);
  const pageTitle = pages.find((page) => page.key === section)?.label ?? "Calendar";
  const canEdit = platform || me.role === "tenant_admin";
  const canBook = canEdit || me.role === "tenant_staff";
  const scope: BookingsScope = {
    tenantId: platform ? tenant : null, canEdit, canBook, zone, zoneState, query: tenantQuery,
  };
  const chooseTenant = (value: string) => {
    router.replace(value ? `${pathname}?client_id=${encodeURIComponent(value)}` : pathname, { scroll: false });
  };

  return (
    <>
      <Heading
        title="Bookings"
        description="A calendar for booking your people, rooms and shared things, for your team and your customers."
      />
      <SectionNav label="Bookings pages" testId="bookings-subnav" items={pages} current={section}
        query={tenantQuery || undefined} />
      <h2 ref={pageHeading} tabIndex={-1} data-testid="bookings-page-heading"
        className="mb-2 font-(family-name:--fs-font-heading) text-(length:--fs-text-xl) font-semibold focus:outline-none sm:mb-3">
        {pageTitle}
      </h2>
      {error && <div className="mb-5"><Notice error>{error}</Notice></div>}
      {platform && (
        <label className="my-5 block text-sm">Tenant
          <select className={`${field} mt-1`} data-testid="bookings-tenant" value={selected}
            onChange={(event) => chooseTenant(event.target.value)}>
            <option value="">Select a tenant</option>
            {(clients ?? []).map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </select>
        </label>
      )}
      {!tenant ? (
        <Notice>Select a tenant to see its bookings.</Notice>
      ) : (
        <div key={`${tenant}:${section}`}>
          {section === "calendar" && <BookingsCalendar scope={scope} />}
          {section === "types" && <BookingTypes scope={scope} peopleHref={pages[2].href} />}
          {section === "people" && <PeopleAndRooms scope={scope} />}
        </div>
      )}
    </>
  );
}
