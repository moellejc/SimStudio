"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ResultViewer } from "@/components/result-viewer";
import { Button } from "@/components/ui/button";
import { formatForceN, formatPa, formatPercent, formatStrain, isActive, statusLabel } from "@/lib/format";

const PartViewport = dynamic(() => import("@/components/part-viewport").then((mod) => mod.PartViewport), {
  ssr: false,
  loading: () => <p className="p-6 text-sm text-[#5c5348]">Opening the part…</p>,
});

type Artifact = { id: string; kind: string; filename: string; sizeBytes: number };
type Simulation = {
  id: string;
  name: string;
  status: string;
  progress: number;
  message: string;
  errorMessage: string | null;
  material: string | null;
  templateId: string | null;
  dryRun: boolean;
  summary: {
    peak_force_N?: number | null;
    buckling_force_N?: number | null;
    max_von_mises_Pa?: number | null;
    max_plastic_strain?: number | null;
    energy_balance_ok?: boolean | null;
    added_mass_pct?: number | null;
    buckling_reason?: string | null;
    fracture_onset?: { load_N?: number; time_s?: number } | null;
    geometry?: { nodes?: number; bricks?: number; volume_mm3?: number; bbox_min_mm?: number[]; bbox_max_mm?: number[] };
    notes?: string[];
  } | null;
  artifacts: Artifact[];
};

type LogLine = { stream?: string; line: string };

export function SimDetail({ id }: { id: string }) {
  const [sim, setSim] = useState<Simulation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [live, setLive] = useState<string>("");

  async function load() {
    const response = await fetch(`/api/simulations/${id}`);
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(body.error || "Simulation not found.");
      return;
    }
    setSim(body.simulation);
    setError(null);
  }

  useEffect(() => {
    load();
    const source = new EventSource(`/api/simulations/${id}/events`);
    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "log") {
        setLogs((current) => [...current.slice(-400), data]);
        return;
      }
      if (data.type === "status" || data.state) {
        setLive(data.message || "");
        setSim((current) =>
          current
            ? {
                ...current,
                status: data.state || current.status,
                progress: Number(data.progress ?? current.progress),
                message: data.message || current.message,
                errorMessage: data.error || current.errorMessage,
              }
            : current,
        );
        if (["succeeded", "failed", "cancelled"].includes(data.state)) load();
      }
    };
    source.onerror = () => setLive((value) => value || "Live updates paused. The page still refreshes the record.");
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function cancel() {
    await fetch(`/api/simulations/${id}/cancel`, { method: "POST" });
  }

  if (error) return <p className="text-sm text-[#9f1239]">{error}</p>;
  if (!sim) return <p className="text-sm text-[#5c5348]">Loading the study…</p>;
  const summary = sim.summary;
  const geometry = summary?.geometry;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-xs text-[#9a3412]">{statusLabel(sim.status)} · {Math.round(sim.progress)}%</p>
          <h1 className="text-3xl">{sim.name}</h1>
          <p className="mt-1 text-sm text-[#5c5348]">
            {sim.material || "uploaded decks"} · {sim.templateId || "template"}
            {sim.dryRun ? " · dry run" : " · OpenMP engine"}
          </p>
          <p className="mt-2 text-sm">{sim.errorMessage || live || sim.message}</p>
        </div>
        <div className="flex gap-2">
          {isActive(sim.status) ? (
            <Button variant="outline" onClick={cancel}>
              Cancel
            </Button>
          ) : null}
          <Button variant="outline" render={<Link href="/simulations" />}>
            Back to runs
          </Button>
        </div>
      </div>
      <div className="h-2 overflow-hidden rounded bg-[#efe8dc]">
        <div className="h-full bg-[#c2410c] transition-all" style={{ width: `${Math.min(100, sim.progress)}%` }} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Peak force" value={formatForceN(summary?.peak_force_N)} hint="Applied load or floor reaction, in N" />
        <Metric label="Buckling force" value={formatForceN(summary?.buckling_force_N)} hint={summary?.buckling_reason || "Null when none is detected"} />
        <Metric label="Max von Mises" value={formatPa(summary?.max_von_mises_Pa)} hint="Pascals" />
        <Metric label="Max plastic strain" value={formatStrain(summary?.max_plastic_strain)} />
      </div>
      <p className="text-sm text-[#5c5348]">
        Energy balance {summary?.energy_balance_ok == null ? "—" : summary.energy_balance_ok ? "within 5%" : "outside 5%"}
        {summary?.added_mass_pct != null ? ` · added mass ${formatPercent(summary.added_mass_pct)}` : ""}
        {summary?.fracture_onset ? ` · fracture at ${formatForceN(summary.fracture_onset.load_N)}` : ""}
      </p>
      {geometry ? (
        <p className="text-sm text-[#5c5348]">
          Mesh {geometry.nodes} nodes, {geometry.bricks} bricks, volume {geometry.volume_mm3?.toFixed(0)} mm³. Bounding box Z{" "}
          {geometry.bbox_min_mm?.[2]} to {geometry.bbox_max_mm?.[2]} mm. Floor is Z = 0; the part stands 1 mm above it and the push is along −Z.
        </p>
      ) : null}
      {sim.status === "succeeded" ? (
        <ResultViewer
          id={sim.id}
          partUrl={sim.artifacts?.some((artifact) => artifact.filename === "model.glb") ? `/api/simulations/${sim.id}/part` : null}
          mp4Url={(() => {
            const clip = sim.artifacts?.find((artifact) => artifact.filename.endsWith(".mp4"));
            return clip ? `/api/simulations/${sim.id}/artifacts/${clip.id}?inline=1` : null;
          })()}
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-[#d9d3c8] bg-white">
          <div className="h-[420px]">
            <PartViewport url="/models/fork.glb" />
          </div>
          <p className="border-t border-[#eee] px-3 py-2 text-xs text-[#666]">
            Template preview while the study runs. It is already meters and Y-up, so it will sit on the result surface with no extra transform. The colored frames open when the glTF export is stored.
          </p>
        </div>
      )}
      <section className="grid gap-4 lg:grid-cols-2">
        <div>
          <h2 className="mb-2 text-lg">Listing</h2>
          <pre className="h-72 overflow-auto rounded-lg bg-[#1c1915] p-3 font-mono text-xs text-[#f3efe6]">
            {logs.length ? logs.map((line, index) => <div key={index}>[{line.stream || "log"}] {line.line}</div>) : "Waiting for the first lines…"}
          </pre>
        </div>
        <div>
          <h2 className="mb-2 text-lg">Files</h2>
          {sim.artifacts?.length ? (
            <ul className="space-y-2 text-sm">
              {sim.artifacts.map((artifact) => (
                <li key={artifact.id} className="flex items-center justify-between gap-3 rounded border border-[#e4d9c8] bg-white px-3 py-2">
                  <span>
                    <span className="font-mono">{artifact.filename}</span>
                    <span className="ml-2 text-[#5c5348]">{artifact.kind}</span>
                  </span>
                  <a className="text-[#9a3412] underline" href={`/api/simulations/${sim.id}/artifacts/${artifact.id}`}>
                    Download
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-[#5c5348]">Artifacts land in object storage when the run finishes.</p>
          )}
        </div>
      </section>
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-[#e4d9c8] bg-white p-3">
      <div className="font-mono text-xs text-[#5c5348]">{label}</div>
      <div className="mt-1 font-mono text-lg">{value}</div>
      {hint ? <div className="mt-1 text-xs text-[#5c5348]">{hint}</div> : null}
    </div>
  );
}
