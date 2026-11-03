"use client";

import Link from "next/link";
import { useState, type AnchorHTMLAttributes, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";
import { currentChild, groupParentCurrent, type NavLink } from "@/lib/navigation";

// The arrow sits inside the row, at its right end, with no box of its own: the parent link's
// hover and current surface runs under it, so the row reads as one item.
const CHEVRON =
  "absolute inset-y-0 right-0 flex w-8 items-center justify-center rounded-r-[10px] text-[color:var(--fs-text-secondary)] outline-offset-[-2px] hover:text-[color:var(--fs-text)] focus-visible:outline-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:w-11";
const CHILD =
  "flex min-h-9 items-center rounded-[10px] py-1.5 pl-5 pr-3 text-sm text-[color:var(--fs-text-secondary)] outline-offset-2 hover:bg-[var(--fs-tile)] hover:text-[color:var(--fs-text)] focus-visible:outline-2 focus-visible:outline-[color:var(--fs-focus)] aria-[current=page]:bg-[var(--fs-surface)] aria-[current=page]:font-semibold aria-[current=page]:text-[color:var(--fs-text)] aria-[current=page]:shadow-[var(--fs-shadow-card)] pointer-coarse:min-h-11";

/**
 * Which groups are open. A group is open while the path belongs to it; the chevron overrides
 * that in client state only (no cookie), and moving into a group drops its override so
 * following a link there shows it again.
 */
export function useGroupOpen(links: NavLink[], path: string) {
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [seen, setSeen] = useState(path);
  const inside = (link: NavLink) =>
    Boolean(link.children?.length) &&
    (path === link.href || currentChild(link.children ?? [], path, link.href) !== null);
  let effective = overrides;
  if (seen !== path) {
    setSeen(path);
    const next = { ...overrides };
    for (const link of links) if (inside(link)) delete next[link.key];
    effective = next;
    setOverrides(next);
  }
  const isOpen = (link: NavLink) => effective[link.key] ?? inside(link);
  const toggle = (link: NavLink) =>
    setOverrides({ ...effective, [link.key]: !isOpen(link) });
  return { isOpen, toggle };
}

/**
 * One rail or menu entry that has sub-pages: the parent link (always a real link to the first
 * sub-page), a chevron button that shows or hides the sub-page list, then the list. The link
 * comes first in the tab order, the button second. While the list is shown the child carries
 * `aria-current="page"` and the parent only a quieter "in this group" mark, so a page never has
 * two current links. The open state lives in the caller.
 */
export function NavGroup({
  link,
  path,
  open,
  onToggle,
  idPrefix,
  linkClass,
  icon,
  label,
  linkProps,
  onNavigate,
}: {
  link: NavLink;
  path: string;
  open: boolean;
  onToggle: () => void;
  idPrefix: "rail" | "menu";
  linkClass: string;
  icon: ReactNode;
  label: ReactNode;
  linkProps?: AnchorHTMLAttributes<HTMLAnchorElement>;
  onNavigate?: () => void;
}) {
  const animate = useMotionAllowed();
  const children = link.children ?? [];
  const current = currentChild(children, path, link.href);
  const listId = `${idPrefix}-group-${link.key}`;
  return (
    <div className="flex flex-col">
      <div className="relative [&:hover>a:not([aria-current=page])]:bg-[var(--fs-tile)] [&:hover>a:not([aria-current=page])]:text-[color:var(--fs-text)]">
        <Link
          {...linkProps}
          href={link.href}
          aria-current={groupParentCurrent(link, path, open)}
          data-group-current={current ? "true" : undefined}
          // After the spread, and running the caller's handler too: neither replaces the other.
          onClick={(event) => {
            linkProps?.onClick?.(event);
            onNavigate?.();
          }}
          className={`${linkClass} w-full min-w-0 pr-7! data-[group-current=true]:font-semibold data-[group-current=true]:text-[color:var(--fs-text)] data-[group-current=true]:[&_svg]:text-[color:var(--fs-primary)]`}
        >
          {icon}
          {label}
        </Link>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={listId}
          aria-label={`${link.label} pages`}
          data-testid={`${idPrefix}-group-${link.key}-toggle`}
          onClick={onToggle}
          className={CHEVRON}
        >
          <ChevronDown
            size={16}
            aria-hidden="true"
            style={{
              transform: open ? "rotate(180deg)" : "none",
              transition: animate ? "transform 200ms var(--fs-ease-out)" : "none",
            }}
          />
        </button>
      </div>
      {/* Closed, the grid row is 0fr: no height, and visibility-hidden (not display:none) so the
          links keep a position (layout probes read it) while staying out of the focus order and
          the accessibility tree; `inert` backs that up. Opening eases the row open unless motion
          is reduced. */}
      <div
        className="grid"
        style={{
          gridTemplateRows: open ? "1fr" : "0fr",
          transition: animate
            ? `grid-template-rows 220ms var(--fs-ease-out), visibility 0s linear ${open ? "0s" : "220ms"}`
            : "none",
          visibility: open ? "visible" : "hidden",
        }}
      >
        <ul
          id={listId}
          data-testid={listId}
          inert={!open}
          className={`m-0 ml-5 flex min-h-0 list-none flex-col gap-0.5 overflow-hidden border-l border-[color:var(--fs-border)] p-0 ${open ? "mt-1" : ""}`}
        >
          {children.map((child) => (
            <li key={child.key}>
              <Link
                href={child.href}
                data-testid={`${idPrefix}-child-${link.key}-${child.key}`}
                aria-current={open && current?.key === child.key ? "page" : undefined}
                onClick={onNavigate}
                className={CHILD}
              >
                {child.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
