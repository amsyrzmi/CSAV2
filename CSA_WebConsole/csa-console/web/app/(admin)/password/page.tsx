import { Heading } from "@/components/console/ui";
import { PasswordPanel } from "@/components/console/PasswordPanel";
import { requireIdentity } from "@/lib/server-identity";

export default async function PasswordPage() {
  const me = await requireIdentity();
  return (
    <>
      <Heading title="Password" description="Change the password you sign in with." />
      <PasswordPanel email={me.user.email} />
    </>
  );
}
