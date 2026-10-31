import Link from "next/link";
import { RunSheet } from "@/components/run-sheet";
import { SiteHeader } from "@/components/site-header";
import { Button } from "@/components/ui/button";

export default function SimulationsPage() {
  return (
    <div className="min-h-screen">
      <SiteHeader />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-3xl">Run sheet</h1>
            <p className="mt-1 text-sm text-[#5c5348]">Status refreshes while a starter or engine is still going.</p>
          </div>
          <Button render={<Link href="/simulations/new" />}>New study</Button>
        </div>
        <RunSheet />
      </main>
    </div>
  );
}
