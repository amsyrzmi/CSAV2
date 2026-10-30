import { Heading } from "@/components/console/ui";
import { PasswordPanel } from "@/components/console/PasswordPanel";

export default function PasswordPage() {
  return (
    <>
      <Heading title="Password" description="Change the password you sign in with." />
      <PasswordPanel />
    </>
  );
}
