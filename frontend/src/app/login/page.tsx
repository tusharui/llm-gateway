import { redirect } from "next/navigation";
import Link from "next/link";
import { getSession, login } from "@/lib/session";

export const dynamic = "force-dynamic";

async function signIn(formData: FormData) {
  "use server";

  const email = String(formData.get("email") || "");
  const password = String(formData.get("password") || "");
  const next = String(formData.get("next") || "/");

  const result = await login(email, password);
  if ("error" in result) {
    redirect(`/login?error=${encodeURIComponent(result.error)}`);
  }

  // Only same-site paths. An attacker-controlled absolute URL here would turn
  // the login page into an open redirect that carries a freshly authenticated
  // session to their own site.
  redirect(next.startsWith("/") && !next.startsWith("//") ? next : "/");
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const { error, next } = await searchParams;

  if (await getSession()) {
    redirect("/");
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-bold tracking-tight mb-8">Sign in</h1>

        {error && (
          <div className="mb-6 border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <form action={signIn} className="space-y-4">
          <input type="hidden" name="next" value={next ?? "/"} />

          <div>
            <label htmlFor="email" className="block text-sm text-zinc-400 mb-1">
              Email
            </label>
            <input
              id="email"
              name="email"
              type="email"
              required
              autoComplete="email"
              className="w-full border border-white/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm text-zinc-400 mb-1">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              required
              autoComplete="current-password"
              className="w-full border border-white/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40"
            />
          </div>

          <button
            type="submit"
            className="w-full bg-white px-4 py-2 text-sm font-medium text-black hover:bg-zinc-200"
          >
            Sign in
          </button>
        </form>

        <p className="mt-6 text-sm text-zinc-500">
          No account?{" "}
          <Link href="/register" className="text-white underline">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}