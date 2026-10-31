"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { SiteHeader } from "@/components/site-header";
import { Button } from "@/components/ui/button";

export default function SignupPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.get("name"),
        email: form.get("email"),
        password: form.get("password"),
      }),
    });
    const body = await response.json().catch(() => ({}));
    setPending(false);
    if (!response.ok) {
      setError(body.error || "Could not create the account.");
      return;
    }
    router.push("/simulations");
    router.refresh();
  }

  return (
    <div className="min-h-screen">
      <SiteHeader />
      <main className="mx-auto max-w-md px-4 py-12">
        <h1 className="text-2xl">Create an account</h1>
        <p className="mt-2 text-sm text-[#5c5348]">
          Already registered? <Link href="/login" className="text-[#9a3412] underline">Sign in</Link>.
        </p>
        <form onSubmit={onSubmit} className="mt-6 space-y-4 rounded-xl border border-[#e4d9c8] bg-[#faf7f2] p-5">
          <label className="block text-sm">
            Name
            <input name="name" required className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2" />
          </label>
          <label className="block text-sm">
            Email
            <input name="email" type="email" required autoComplete="email" className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2" />
          </label>
          <label className="block text-sm">
            Password
            <input name="password" type="password" required minLength={8} autoComplete="new-password" className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2" />
          </label>
          {error ? <p className="text-sm text-[#9f1239]">{error}</p> : null}
          <Button type="submit" disabled={pending} className="w-full">
            {pending ? "Creating…" : "Create account"}
          </Button>
        </form>
      </main>
    </div>
  );
}
