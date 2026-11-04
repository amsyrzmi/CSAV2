/** Console themes. The server reads the cookie so the first response already carries data-theme. */
export const THEMES = ["cobalt", "night", "bureau"] as const;
export type Theme = (typeof THEMES)[number];

export const THEME_COOKIE = "fs-theme";
const ONE_YEAR_SECONDS = 31536000;

/** Exact, case-sensitive match against the known names; anything else is the default. */
export function parseTheme(raw: string | null | undefined): Theme {
  return THEMES.find((name) => name === raw) ?? "cobalt";
}

export function themeCookie(theme: Theme): string {
  return `${THEME_COOKIE}=${theme}; Path=/; Max-Age=${ONE_YEAR_SECONDS}; SameSite=Lax`;
}

/** Client only: persist the choice and apply it at once, without a reload. */
export function writeTheme(theme: Theme): void {
  document.cookie = themeCookie(theme);
  try {
    localStorage.setItem(THEME_COOKIE, theme);
  } catch {
    // Storage can be blocked; the cookie and the attribute still carry the choice.
  }
  document.documentElement.dataset.theme = theme;
}
