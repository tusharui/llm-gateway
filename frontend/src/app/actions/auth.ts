"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

export async function signOut() {
  const { logout } = await import("@/lib/session");
  await logout();
  revalidatePath("/", "layout");
  redirect("/login");
}