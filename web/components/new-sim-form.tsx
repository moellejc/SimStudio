"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";

export function NewSimForm() {
  const router = useRouter();
  const [mode, setMode] = useState<"template" | "upload">("template");
  const [templateId, setTemplateId] = useState("fork_load");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    let response: Response;
    if (mode === "upload") {
      response = await fetch("/api/simulations", { method: "POST", body: form });
    } else {
      response = await fetch("/api/simulations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.get("name"),
          templateId,
          material: form.get("material"),
          dryRun: form.get("dryRun") === "on",
          endTimeMs: Number(form.get("endTimeMs")),
          loadKN: Number(form.get("loadKN")),
          stopDisplacementMm: Number(form.get("stopDisplacementMm")),
          pushVelocityMmMs: Number(form.get("pushVelocityMmMs")),
          rampMs: Number(form.get("rampMs")),
          threads: Number(form.get("threads")),
          friction: Number(form.get("friction")),
        }),
      });
    }
    const body = await response.json().catch(() => ({}));
    setPending(false);
    if (!response.ok) {
      setError(body.error || "The study was not accepted.");
      if (body.id) router.push(`/simulations/${body.id}`);
      return;
    }
    router.push(`/simulations/${body.simulation.id}`);
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5 rounded-xl border border-[#e4d9c8] bg-[#faf7f2] p-5">
      <div className="flex gap-2 text-sm">
        <button type="button" className={`rounded-md px-3 py-1 ${mode === "template" ? "bg-[#9a3412] text-white" : "bg-white"}`} onClick={() => setMode("template")}>
          Fork template
        </button>
        <button type="button" className={`rounded-md px-3 py-1 ${mode === "upload" ? "bg-[#9a3412] text-white" : "bg-white"}`} onClick={() => setMode("upload")}>
          Upload decks
        </button>
      </div>
      <label className="block text-sm">
        Name
        <input name="name" required defaultValue="304 stainless, 45 kg" className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2" />
      </label>
      {mode === "template" ? (
        <>
          <fieldset className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              Study
              <select value={templateId} onChange={(event) => setTemplateId(event.target.value)} className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2">
                <option value="fork_load">Force control (gen_deck45)</option>
                <option value="fork_displacement">Imposed velocity (gen_deck)</option>
              </select>
            </label>
            <label className="block text-sm">
              Material
              <select name="material" defaultValue="steel" className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2">
                <option value="steel">304 stainless</option>
                <option value="aluminum">6061-T6 aluminum</option>
                <option value="plastic">Polystyrene</option>
              </select>
            </label>
            <Field name="loadKN" label="Load (kN)" defaultValue="0.4413" />
            <Field name="pushVelocityMmMs" label="Push speed (mm/ms)" defaultValue="0.25" />
            <Field name="endTimeMs" label="End time (ms)" defaultValue="20" />
            <Field name="rampMs" label="Ramp (ms)" defaultValue="100" />
            <Field name="stopDisplacementMm" label="Stop travel (mm)" defaultValue="40" />
            <Field name="friction" label="Wall friction" defaultValue="0.3" />
            <Field name="threads" label="OpenMP threads" defaultValue="2" />
          </fieldset>
          <p className="text-sm text-[#5c5348]">
            Units are kg, mm, ms. 0.4413 kN is 45 kg times gravity. Stress in the deck is GPa; the summary stores pascals.
          </p>
        </>
      ) : (
        <div className="grid gap-3">
          <label className="block text-sm">
            Starter (*_0000.rad)
            <input name="starter" type="file" accept=".rad" required className="mt-1 block w-full text-sm" />
          </label>
          <label className="block text-sm">
            Engine (*_0001.rad)
            <input name="engine" type="file" accept=".rad" required className="mt-1 block w-full text-sm" />
          </label>
        </div>
      )}
      <label className="flex items-start gap-2 text-sm">
        <input name="dryRun" type="checkbox" defaultChecked className="mt-1" />
        <span>
          Dry run. Synthesize the listing and, if the converters are absent, play the sample surface. Leave this on unless
          <span className="font-mono"> engine_linux64_gf</span> is installed. The MPI engine is never launched.
        </span>
      </label>
      {error ? <p className="text-sm text-[#9f1239]">{error}</p> : null}
      <Button type="submit" disabled={pending}>
        {pending ? "Submitting…" : "Submit study"}
      </Button>
    </form>
  );
}

function Field({ name, label, defaultValue }: { name: string; label: string; defaultValue: string }) {
  return (
    <label className="block text-sm">
      {label}
      <input name={name} defaultValue={defaultValue} inputMode="decimal" required className="mt-1 w-full rounded-md border border-[#e4d9c8] bg-white px-3 py-2 font-mono" />
    </label>
  );
}
