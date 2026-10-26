import { redirect } from "next/navigation";

/** The assistant dock on every tab replaced the Staff workspace page. */
export default function WorkspacePage() {
  redirect("/");
}
