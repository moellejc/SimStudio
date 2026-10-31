"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { formatForceN, formatPa, isActive, statusLabel } from "@/lib/format";
import { Button } from "@/components/ui/button";

type Row = {
  id: string;
  name: string;
  status: string;
  progress: number;
  material: string | null;
  templateId: string | null;
  dryRun: boolean;
  createdAt: string;
  peakForceN: number | null;
  maxVonMisesPa: number | null;
  message: string;
};

export function RunSheet() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    async function load() {
      const response = await fetch("/api/simulations");
      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (!stop) setError(body.error || "Could not load runs.");
        return;
      }
      if (!stop) {
        setRows(body.simulations);
        setError(null);
      }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, []);

  if (error) return <p className="text-sm text-[#9f1239]">{error}</p>;
  if (!rows) return <p className="text-sm text-[#5c5348]">Loading the run sheet…</p>;
  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[#e4d9c8] bg-white p-8">
        <h2 className="text-xl">No studies yet</h2>
        <p className="mt-2 max-w-lg text-sm text-[#5c5348]">
          Start with the 45 kg fork load on stainless, aluminum, or polystyrene. Dry run is enough to see force,
          von Mises, and the mesh viewer.
        </p>
        <Button className="mt-4" render={<Link href="/simulations/new" />}>
          New study
        </Button>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-[#e4d9c8] bg-white">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead className="border-b border-[#e4d9c8] font-mono text-xs text-[#5c5348]">
          <tr>
            <th className="px-4 py-3 font-medium">Study</th>
            <th className="px-4 py-3 font-medium">State</th>
            <th className="px-4 py-3 font-medium">Peak force</th>
            <th className="px-4 py-3 font-medium">Max von Mises</th>
            <th className="px-4 py-3 font-medium">Opened</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-[#f0e7da] last:border-0">
              <td className="px-4 py-3">
                <Link href={`/simulations/${row.id}`} className="font-medium text-[#9a3412] hover:underline">
                  {row.name}
                </Link>
                <div className="text-xs text-[#5c5348]">
                  {row.material || "upload"} · {row.templateId === "fork_displacement" ? "imposed velocity" : row.templateId === "upload" ? "uploaded decks" : "45 kg load"}
                  {row.dryRun ? " · dry run" : ""}
                </div>
              </td>
              <td className="px-4 py-3">
                <span className={isActive(row.status) ? "text-[#9a3412]" : ""}>{statusLabel(row.status)}</span>
                {isActive(row.status) ? <div className="mt-1 h-1 w-24 overflow-hidden rounded bg-[#efe8dc]"><div className="h-full bg-[#c2410c]" style={{ width: `${Math.min(100, row.progress)}%` }} /></div> : null}
              </td>
              <td className="px-4 py-3 font-mono">{formatForceN(row.peakForceN)}</td>
              <td className="px-4 py-3 font-mono">{formatPa(row.maxVonMisesPa)}</td>
              <td className="px-4 py-3 text-[#5c5348]">{new Date(row.createdAt).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
