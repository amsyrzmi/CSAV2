"use client";

import Link from "next/link";
import type { NavLink } from "@/lib/navigation";
import { activeNav } from "@/lib/shell-state";
import { MitoMark } from "@/components/brand/Mito";
import { MobileMenu } from "./MobileMenu";
import { SignOutButton, UserMenu, type ShellAccount } from "./UserMenu";
import { ThemeControl } from "./ThemeControl";

/**
 * The strip above every page: menu button and brand mark below 1024 px, the page title taken
 * from the active navigation label, the theme control and the user menu. The title is a plain
 * paragraph: each page renders its own h1, and a second heading would double it.
 */
export function TopBar({
  links,
  path,
  home,
  fallbackTitle,
  account,
}: {
  links: NavLink[];
  path: string;
  home: string;
  fallbackTitle: string;
  account: ShellAccount;
}) {
  const title = activeNav(links, path)?.label ?? fallbackTitle;
  return (
    <header
      data-testid="shell-topbar"
      className="flex items-center gap-3 border-b border-[color:var(--fs-border)] px-4 py-3 sm:px-8"
    >
      <MobileMenu links={links} path={path} />
      <Link href={home} aria-label="Mito home" className="flex items-center lg:hidden">
        <MitoMark size={32} />
      </Link>
      <p data-testid="shell-title" className="min-w-0 flex-1 truncate text-sm font-semibold">
        {title}
      </p>
      <ThemeControl />
      <UserMenu account={account} />
      <SignOutButton
        account={account}
        className="hidden h-10 items-center gap-2 rounded-[var(--fs-radius-control)] px-3 text-sm font-medium text-[color:var(--fs-text-secondary)] hover:bg-[var(--fs-tile)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:opacity-60 lg:flex"
      />
    </header>
  );
}
