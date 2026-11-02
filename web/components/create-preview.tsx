"use client";

import dynamic from "next/dynamic";

const PartViewport = dynamic(() => import("@/components/part-viewport").then((mod) => mod.PartViewport), {
  ssr: false,
  loading: () => <p className="p-6 text-sm text-[#5c5348]">Opening the part…</p>,
});

export function CreatePreview() {
  return (
    <section className="overflow-hidden rounded-xl border border-[#d9d3c8] bg-white">
      <div className="border-b border-[#eee] px-3 py-2 text-sm">Part preview</div>
      <div className="h-[560px]">
        <PartViewport url="/models/fork.glb" />
      </div>
      <p className="border-t border-[#eee] px-3 py-2 text-xs text-[#666]">
        Meters, Y-up. Deck millimeters map as (x, z, −y) / 1000, the same mapping as the result surface, so the two overlay in one scene. A push along solver −Z points down (−Y).
      </p>
    </section>
  );
}
