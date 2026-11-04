import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Inter, Manrope, Space_Grotesk } from "next/font/google";
import { parseTheme, THEME_COOKIE } from "../lib/theme";
import { PAUSE_KEY } from "../lib/motion";
import "./globals.css";

// Self-hosted at build time by next/font; each font exposes a CSS variable that
// globals.css maps onto the --fs-font-* tokens, with system fallbacks.
const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-space-grotesk",
});
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});
const manrope = Manrope({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-manrope",
});

export const metadata: Metadata = {
  title: "Mito by fourstacks",
  description: "Customer service assistant operations console",
  robots: { index: false, follow: false },
};

// Runs before first paint: marks the document Calm when the stored pause preference is on, so CSS
// (the unhydrated loading screen, the auth logo) can stay still. data-calm is only a pre-hydration
// hint: the hooks stay the source of truth once mounted. A future CSP needs a nonce (or a hash)
// for this inline script.
const CALM_SCRIPT = `try{if(window.localStorage.getItem(${JSON.stringify(PAUSE_KEY)})==="1")document.documentElement.setAttribute("data-calm","1")}catch(e){}`;

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Read on the server so the first response already carries the theme (no flash).
  // parseTheme only ever returns a known name, so a tampered cookie cannot reach the HTML.
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html
      lang="en"
      data-theme={theme}
      suppressHydrationWarning
      className={`${spaceGrotesk.variable} ${inter.variable} ${manrope.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: CALM_SCRIPT }} />
      </head>
      <body className="fs-ui min-h-screen">{children}</body>
    </html>
  );
}
