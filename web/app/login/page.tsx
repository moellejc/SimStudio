"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { SiteHeader } from "@/components/site-header";
import { Button } from "@/components/ui/button";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    const body = await response.json().catch(() => ({}));
    setPending(false);
    if (!response.ok) {
      setError(body.error || "Could not sign in.");
      return;
    }
    router.push(params.get("next") || "/simulations");
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <label className="block text-sm">
        Email
        <input name="email" type="email" required autoComplete="email" className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2" />
      </label>
      <label className="block text-sm">
        Password
        <input name="password" type="password" required autoComplete="current-password" className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2" />
      </label>
      {error ? <p className="text-sm text-[#9f1239]">{error}</p> : null}
      <Button type="submit" disabled={pending} className="w-full">
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="min-h-screen">
      <SiteHeader />
      <main className="mx-auto max-w-md px-4 py-12">
        <h1 className="text-2xl">Sign in</h1>
        <p className="mt-2 text-sm text-[#5c5348]">
          No account yet? <Link href="/signup" className="text-[#9a3412] underline">Create one</Link>.
        </p>
        <div className="mt-6 rounded-xl border border-[#e4d9c8] bg-[#faf7f2] p-5">
          <Suspense fallback={<p className="text-sm">Loading…</p>}>
            <LoginForm />
          </Suspense>
        </div>
      </main>
    </div>
  );
}
