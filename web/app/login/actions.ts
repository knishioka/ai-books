"use server";

import { redirect } from "next/navigation";

import { isAllowedEmail, safeNextPath } from "@/lib/auth/allowlist";
import { getAllowedEmail } from "@/lib/auth/env";
import { createClient } from "@/lib/supabase/server";

/**
 * Sign in with email + password (issue #108). Single user: there is no sign-up — the owner
 * is provisioned in the Supabase dashboard. The single-user allowlist is enforced here at
 * sign-in *and* in the middleware on every request (defence in depth, fail closed).
 */
export async function signIn(formData: FormData) {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = safeNextPath(String(formData.get("next") ?? "/"));
  const withParams = (error: string) =>
    `/login?error=${error}&next=${encodeURIComponent(next)}`;

  const supabase = await createClient();
  if (!supabase) {
    redirect(withParams("unconfigured"));
  }

  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  });
  if (error) {
    redirect(withParams("invalid"));
  }

  if (!isAllowedEmail(data.user?.email, getAllowedEmail())) {
    // A valid Supabase identity that is not the owner — deny and drop the session.
    await supabase.auth.signOut();
    redirect(withParams("forbidden"));
  }

  redirect(next);
}

/** Sign out and return to the login screen. */
export async function signOut() {
  const supabase = await createClient();
  if (supabase) {
    await supabase.auth.signOut();
  }
  redirect("/login");
}
