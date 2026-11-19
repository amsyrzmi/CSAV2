"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Menu } from "lucide-react";
import SlideOver from "@/components/console/fs/SlideOver";
import type { NavBadges, NavLink } from "@/lib/navigation";
import { badgeLinkName } from "@/lib/shell-counts";
import { navIcon } from "./icons";
import { NavCountBadge } from "./NavCountBadge";
import { NavGroup, useGroupOpen } from "./NavGroup";

const ITEM =
  "flex min-h-11 items-center gap-3 rounded-[10px] px-3 text-sm font-medium text-[color:var(--fs-text-secondary)] hover:bg-[var(--fs-tile)] hover:text-[color:var(--fs-text)] aria-[current=page]:bg-[var(--fs-surface-blue)] aria-[current=page]:font-semibold aria-[current=page]:text-[color:var(--fs-text)] aria-[current=page]:[&_svg]:text-[color:var(--fs-primary)]";

/**
 * Below 1024 px the rail is hidden and this button opens the full labelled list on a native
 * <dialog> (SlideOver gives Escape, the inert page behind it and focus back to the button).
 * Following a link closes it.
 */
export function MobileMenu({
  links,
  path,
  badges,
}: {
  links: NavLink[];
  path: string;
  badges?: NavBadges;
}) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const groups = useGroupOpen(links, path);

  useEffect(() => {
    // Widening to desktop shows the rail, so a dialog left open would only hide it.
    const wide = window.matchMedia("(min-width: 1024px)");
    const close = () => {
      if (wide.matches) setOpen(false);
    };
    wide.addEventListener("change", close);
    return () => wide.removeEventListener("change", close);
  }, []);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        data-testid="shell-menu"
        aria-label="Open navigation menu"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        className="flex h-11 w-11 items-center justify-center rounded-[10px] border border-[color:var(--fs-border-control)] text-[color:var(--fs-text)] hover:bg-[var(--fs-tile)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] lg:hidden"
      >
        <Menu size={20} aria-hidden="true" />
      </button>
      <SlideOver
        open={open}
        onClose={() => setOpen(false)}
        title="Menu"
        returnFocusTo={buttonRef}
      >
        <nav aria-label="Main navigation" className="flex flex-col gap-1">
          {links.map((link) => {
            const { href, label, key } = link;
            const Icon = navIcon(key);
            if (link.children?.length)
              return (
                <NavGroup
                  key={href}
                  link={link}
                  path={path}
                  open={groups.isOpen(link)}
                  onToggle={() => groups.toggle(link)}
                  idPrefix="menu"
                  linkClass={ITEM}
                  icon={<Icon size={18} aria-hidden="true" />}
                  label={label}
                  onNavigate={() => setOpen(false)}
                />
              );
            const count = badges?.[key];
            const countName = badgeLinkName(label, count);
            return (
              <Link
                key={href}
                href={href}
                aria-label={countName === label ? undefined : countName}
                aria-current={path === href ? "page" : undefined}
                onClick={() => setOpen(false)}
                className={ITEM}
              >
                <Icon size={18} aria-hidden="true" />
                <span className="min-w-0">{label}</span>
                <NavCountBadge navKey={key} count={count} className="ml-auto" />
              </Link>
            );
          })}
        </nav>
      </SlideOver>
    </>
  );
}
