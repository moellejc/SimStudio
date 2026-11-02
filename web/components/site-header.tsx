"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type User = { name: string; email: string };

export function SiteHeader() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    fetch("/api/auth/me")
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => setUser(body?.user ?? null))
      .catch(() => setUser(null));
  }, []);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/");
    router.refresh();
  }

  return (
    <header className="border-b border-[#e4d9c8] bg-[#faf7f2]/90">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
        <Link href={user ? "/simulations" : "/"} className="flex items-baseline gap-2">
          <span className="font-mono text-sm tracking-[0.18em] text-[#9a3412]">SIMSTUDIO</span>
          <span className="hidden text-sm text-[#5c5348] sm:inline">OpenRadioss workbench</span>
        </Link>
        <nav className="flex items-center gap-2 text-sm">
          {user ? (
            <>
              <Link href="/simulations" className="px-2 py-1 text-[#3f382f] hover:text-[#9a3412]">
                Runs
              </Link>
              <Link href="/simulations/new" className="px-2 py-1 text-[#3f382f] hover:text-[#9a3412]">
                New study
              </Link>
              <span className="hidden px-2 text-[#5c5348] sm:inline">{user.name}</span>
              <Button variant="outline" size="sm" onClick={logout}>
                Sign out
              </Button>
            </>
          ) : (
            <>
              <Link href="/login" className="px-2 py-1">
                Sign in
              </Link>
              <Button size="sm" render={<Link href="/signup" />}>
                Create account
              </Button>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
