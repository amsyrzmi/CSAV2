import { NextRequest, NextResponse } from "next/server";
import { isHrPagePath } from "./lib/hr-paths";
import { isTrainingPagePath } from "./lib/training-view";

const publicPages = new Set(["/login", "/invite", "/forgot-password"]);
const adoptedPages = new Set([
  "/",
  "/change-password",
  "/password",
  "/portal/password",
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
  "/knowledge-base/add",
  "/knowledge-base/questions",
  "/knowledge-base/maintenance",
  "/portal/faq",
  "/portal/faq/add",
  "/portal/faq/questions",
  "/portal/faq/maintenance",
  // The Sandbox assistant folded into Overview: these only redirect there (keeping client_id).
  "/chat",
  "/portal/chat",
  "/inbox",
  "/portal/inbox",
  "/hr",
  "/portal/hr",
  "/business",
  "/business/products",
  "/business/stores",
  "/portal/business",
  "/portal/business/products",
  "/portal/business/stores",
  "/subscriptions",
  "/portal/subscriptions",
  "/bookings",
  "/bookings/types",
  "/bookings/people-and-rooms",
  "/portal/bookings",
  "/portal/bookings/types",
  "/portal/bookings/people-and-rooms",
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
  if (!adoptedPages.has(path) && !isHrPagePath(path) && !isTrainingPagePath(path))
    return new NextResponse("Not found", { status: 404 });
  // Navigation only; CSAV2 verifies the session and role in every request.
  if (!req.cookies.has("csa_session"))
    return NextResponse.redirect(new URL("/login", req.url));
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|brand/|fourstacks/).*)"],
};
