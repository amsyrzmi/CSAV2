"use client";

import Link from "next/link";
import { useState, type AnchorHTMLAttributes, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";
import { currentChild, type NavLink } from "@/lib/navigation";

const CHEVRON =
  "flex h-10 w-8 shrink-0 items-center justify-center rounded-[10px] text-[color:var(--fs-text-secondary)] outline-offset-2 hover:bg-[var(--fs-tile)] hover:text-[color:var(--fs-text)] focus-visible:outline-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:h-11 pointer-coarse:w-11";
const CHILD =
  "flex min-h-9 items-center rounded-[10px] py-1.5 pl-10 pr-3 text-sm text-[color:var(--fs-text-secondary)] outline-offset-2 hover:bg-[var(--fs-tile)] hover:text-[color:var(--fs-text)] focus-visible:outline-2 focus-visible:outline-[color:var(--fs-focus)] aria-[current=page]:bg-[var(--fs-surface)] aria-[current=page]:font-semibold aria-[current=page]:text-[color:var(--fs-text)] aria-[current=page]:shadow-[var(--fs-shadow-card)] pointer-coarse:min-h-11";

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
      <div className="flex items-center gap-1">
        <Link
          {...linkProps}
          href={link.href}
          aria-current={path === link.href && !open ? "page" : undefined}
          data-group-current={current ? "true" : undefined}
          onClick={onNavigate}
          className={`${linkClass} min-w-0 flex-1 data-[group-current=true]:font-semibold data-[group-current=true]:text-[color:var(--fs-text)] data-[group-current=true]:[&_svg]:text-[color:var(--fs-primary)]`}
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
              transition: animate ? "transform 150ms var(--fs-ease-out)" : "none",
            }}
          />
        </button>
      </div>
      <ul
        id={listId}
        data-testid={listId}
        // Closed it is collapsed to no height and visibility-hidden, not display:none, so the
        // links keep a position (layout probes read it) while staying out of the focus order
        // and the accessibility tree; `inert` backs that up.
        inert={!open}
        style={open ? undefined : { visibility: "hidden", height: 0, overflow: "hidden" }}
        className={`m-0 flex list-none flex-col gap-0.5 p-0 ${open ? "mt-1" : ""}`}
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
  );
}
