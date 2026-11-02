"use client";

import dynamic from "next/dynamic";
import { useCallback, useRef, useState } from "react";
import { defaultFrame, type SurfaceContract } from "@/lib/glb-contract";

const GltfViewport = dynamic(() => import("@/components/gltf-viewport").then((mod) => mod.GltfViewport), {
  ssr: false,
  loading: () => <p className="p-6 text-sm text-[#5c5348]">Opening the 3D view…</p>,
});

export function ResultViewer({ id, partUrl, mp4Url }: { id: string; partUrl: string | null; mp4Url: string | null }) {
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [showPart, setShowPart] = useState(false);
  const [field, setField] = useState<"stress" | "strain">("stress");
  const [contract, setContract] = useState<SurfaceContract | null>(null);
  const frames = contract?.frameCount ?? 1;
  const touched = useRef(false);
  const contractRef = useRef<SurfaceContract | null>(null);
  const onContract = useCallback((next: SurfaceContract) => {
    contractRef.current = next;
    setContract(next);
  }, []);
  // Open on the peak-load frame (or the max von Mises frame) once the surface is parsed.
  const onPeaks = useCallback((peaks: number[]) => {
    if (touched.current || !contractRef.current) return;
    setFrame(defaultFrame(contractRef.current, peaks));
  }, []);
  const onFrame = useCallback((next: number) => setFrame(next), []);
  const onPlaybackEnd = useCallback(() => setPlaying(false), []);

  const lo = field === "strain" ? (contract?.plasticStrainMin ?? 0) : (contract?.vonMisesMin ?? 0);
  const hi = field === "strain" ? (contract?.plasticStrainMax ?? 1) : (contract?.vonMisesMax ?? 1);
  const unit = field === "strain" ? "plastic strain" : `von Mises (${contract?.stressUnit ?? "Pa"})`;
  const current = contract?.frames[Math.min(frame, Math.max(frames - 1, 0))];
  const load = current?.loadN == null ? "—" : `${current.loadN.toFixed(0)} N`;
  const millis = current ? (current.animTime * 1000).toFixed(1) : "0.0";

  function step(delta: number) {
    touched.current = true;
    setPlaying(false);
    setFrame((value) => (value + delta + frames) % frames);
  }

  return (
    <div className="overflow-hidden rounded-xl border border-[#d9d3c8] bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-[#eee] px-3 py-2 text-sm">
        <button type="button" className="rounded border px-2 py-1" onClick={() => step(-1)}>
          Prev
        </button>
        <button type="button" className="rounded border px-2 py-1" onClick={() => {
            touched.current = true;
            setPlaying((value) => !value);
          }}>
          {playing ? "Pause" : "Play"}
        </button>
        <button type="button" className="rounded border px-2 py-1" onClick={() => step(1)}>
          Next
        </button>
        <input
          className="min-w-32 flex-1"
          type="range"
          min={0}
          max={Math.max(frames - 1, 0)}
          value={Math.min(frame, frames - 1)}
          onChange={(event) => {
            touched.current = true;
            setPlaying(false);
            setFrame(Number(event.target.value));
          }}
        />
        <span className="font-mono text-xs">
          frame {Math.min(frame, frames - 1) + 1}/{frames} · {millis} ms · {load}
        </span>
        <select value={field} onChange={(event) => setField(event.target.value as "stress" | "strain")} className="rounded border px-2 py-1">
          <option value="stress">von Mises stress</option>
          <option value="strain" disabled={!contract?.hasPlasticStrain}>
            plastic strain
          </option>
        </select>
        <label className="flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={showPart}
            title={partUrl ? "Input model (model.glb)" : "Frame-1 surface of the result (no input model stored)"}
            onChange={(event) => setShowPart(event.target.checked)}
          />
          Undeformed part
        </label>
      </div>
      <div className="relative h-[520px]">
        <GltfViewport
          url={`/api/simulations/${id}/model`}
          partUrl={showPart ? partUrl : null}
          showPart={showPart}
          frame={frame}
          playing={playing}
          field={field}
          onContract={onContract}
          onFrame={onFrame}
          onPlaybackEnd={onPlaybackEnd}
          onPeaks={onPeaks}
        />
        <Legend lo={lo} hi={hi} unit={unit} />
      </div>
      <p className="border-t border-[#eee] px-3 py-2 text-xs text-[#666]">
        Drag to orbit, scroll to zoom, right-drag to pan. Playback follows the STEP clip (slowed with timeScale) and holds the last frame.
        Colors are redrawn each frame from the lower-cased <span className="font-mono">_von_mises_####</span> attribute; COLOR_0 is only the last frame.
        {contract
          ? ` Axes: ${contract.lengthUnit}, up ${contract.upAxis}, ${contract.sourceToGltf}, scale ${contract.scale}. Solver −Z points down (−Y).`
          : ""}{" "}
        Fractured elements are collapsed onto their centroids, so those faces have no area. The undeformed part uses the same mapping and needs no extra transform.
      </p>
      {mp4Url ? (
        <details className="border-t border-[#eee] px-3 py-2 text-sm">
          <summary>Quick preview (MP4)</summary>
          <video className="mt-2 max-h-64 w-full" controls src={mp4Url} />
        </details>
      ) : (
        <p className="border-t border-[#eee] px-3 py-2 text-xs text-[#666]">The MP4 heat-map stays a downloadable extra when the post step writes one.</p>
      )}
    </div>
  );
}

function Legend({ lo, hi, unit }: { lo: number; hi: number; unit: string }) {
  const fmt = (value: number) => (Math.abs(value) >= 1000 || (value !== 0 && Math.abs(value) < 0.01) ? value.toExponential(2) : value.toPrecision(3));
  return (
    <div className="pointer-events-none absolute bottom-3 left-1/2 w-[min(420px,90%)] -translate-x-1/2 rounded bg-white/90 px-3 py-2 text-center text-[11px] text-[#111]">
      <div>{unit}</div>
      <div
        className="mt-1 h-3 rounded"
        style={{ background: "linear-gradient(to right, rgb(0,0,128), rgb(0,128,255), rgb(128,255,128), rgb(255,128,0), rgb(128,0,0))" }}
      />
      <div className="mt-1 flex justify-between font-mono">
        <span>{fmt(lo)}</span>
        <span>{fmt((lo + hi) / 2)}</span>
        <span>{fmt(hi)}</span>
      </div>
    </div>
  );
}
