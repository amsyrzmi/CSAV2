import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CSA Console | fourstacks",
  description: "Customer service assistant operations console",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="fs-ui min-h-screen">{children}</body>
    </html>
  );
}
