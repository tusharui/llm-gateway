import { redirect } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import PageTransition from "@/components/PageTransition";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Gate for every dashboard page.
 *
 * The previous version put this in the root layout and rendered the sidebar
 * only when a user came back, falling through to a bare page otherwise. That
 * produced a navless dashboard with no Sign out and no way back to /login --
 * a stale or expired cookie stranded the user instead of signing them out.
 *
 * A route group fixes it: /login and /register sit outside it and render
 * bare, so this layout can redirect unconditionally without looping.
 */
export default async function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const user = await getSession();
  if (!user) {
    redirect("/login");
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar email={user.email} isAdmin={user.is_admin} />
      <main className="flex-1 md:ml-64">
        <PageTransition>{children}</PageTransition>
      </main>
    </div>
  );
}