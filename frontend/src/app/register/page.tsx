import { redirect } from "next/navigation";
import Link from "next/link";
import { getSession, register } from "@/lib/session";

export const dynamic = "force-dynamic";

async function signUp(formData: FormData) {
  "use server";

  const email = String(formData.get("email") || "");
  const password = String(formData.get("password") || "");
  const confirm = String(formData.get("confirm") || "");

  if (password !== confirm) {
    redirect("/register?error=Passwords+do+not+match");
  }

  const result = await register(email, password);
  if ("error" in result) {
    redirect(`/register?error=${encodeURIComponent(result.error)}`);
  }

  redirect("/");
}

export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  if (await getSession()) {
    redirect("/");
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-bold tracking-tight mb-8">Create an account</h1>

        {error && (
          <div className="mb-6 border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <form action={signUp} className="space-y-4">
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
              minLength={8}
              autoComplete="new-password"
              className="w-full border border-white/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40"
            />
            <p className="mt-1 text-xs text-zinc-600">At least 8 characters.</p>
          </div>

          <div>
            <label htmlFor="confirm" className="block text-sm text-zinc-400 mb-1">
              Confirm password
            </label>
            <input
              id="confirm"
              name="confirm"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              className="w-full border border-white/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40"
            />
          </div>

          <button
            type="submit"
            className="w-full bg-white px-4 py-2 text-sm font-medium text-black hover:bg-zinc-200"
          >
            Create account
          </button>
        </form>

        <p className="mt-6 text-sm text-zinc-500">
          Already have an account?{" "}
          <Link href="/login" className="text-white underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}