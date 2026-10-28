import { NextRequest, NextResponse } from "next/server";

const publicPages = new Set(["/login", "/invite", "/forgot-password"]);
const adoptedPages = new Set([
  "/",
  "/change-password",
  "/settings/clients",
  "/settings/team",
  "/portal",
  "/portal/team",
  "/agents",
  "/activity",
  "/portal/agents",
  "/services",
  "/portal/services",
  "/portal/activity",
  "/knowledge-base",
  "/portal/faq",
  "/chat",
  "/portal/chat",
  "/inbox",
  "/portal/inbox",
  "/hr",
  "/portal/hr",
  "/business",
  "/portal/business",
  "/subscriptions",
  "/portal/subscriptions",
  "/workspace",
  "/portal/workspace",
  "/email",
  "/portal/email",
]);

export function proxy(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (path.startsWith("/console/v1/")) return NextResponse.next();
  if (publicPages.has(path)) return NextResponse.next();
  // The legacy API and all deferred UI routes are deliberately inaccessible.
  if (!adoptedPages.has(path))
    return new NextResponse("Not found", { status: 404 });
  // Navigation only; CSAV2 verifies the session and role in every request.
  if (!req.cookies.has("csa_session"))
    return NextResponse.redirect(new URL("/login", req.url));
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|brand/|fourstacks/).*)"],
};
