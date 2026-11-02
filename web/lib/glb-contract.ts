/** Read mesh extras from a simstudio-morph-v1 binary glTF. */

export type SurfaceFrame = {
  index: number;
  animTime: number;
  loadTime: number;
  loadN: number | null;
  eroded: number;
};

export type SurfaceContract = {
  format: string;
  vertexCount: number;
  triangleCount: number;
  frameCount: number;
  times: number[];
  frames: SurfaceFrame[];
  timeAxis: string;
  lengthUnit: string;
  upAxis: string;
  sourceToGltf: string;
  scale: number;
  vonMisesMin: number;
  vonMisesMax: number;
  plasticStrainMin: number;
  plasticStrainMax: number;
  hasPlasticStrain: boolean;
  stressUnit: string;
  /** Last physical load_time (s) when the export marks later frames unphysical; null when not given. */
  physicalUntil: number | null;
};

type GltfJson = {
  animations?: { samplers?: { interpolation?: string }[] }[];
  meshes: {
    extras?: Record<string, unknown>;
    primitives: { attributes: Record<string, number>; targets?: unknown[] }[];
  }[];
};

export function jet(t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, t));
  const channel = (value: number) => Math.min(1, Math.max(0, value));
  return [channel(1.5 - Math.abs(4 * x - 3)), channel(1.5 - Math.abs(4 * x - 2)), channel(1.5 - Math.abs(4 * x - 1))];
}

export function parseSurfaceContract(buffer: ArrayBuffer): SurfaceContract {
  const bytes = new Uint8Array(buffer);
  const jsonLength = new DataView(buffer).getUint32(12, true);
  const json = JSON.parse(new TextDecoder().decode(bytes.slice(20, 20 + jsonLength))) as GltfJson;
  const mesh = json.meshes?.[0];
  const extras = mesh?.extras;
  const primitive = mesh?.primitives?.[0];
  if (!extras || extras.format !== "simstudio-morph-v1" || !primitive) {
    throw new Error("This glTF is not a simstudio-morph-v1 surface.");
  }
  if (json.animations?.[0]?.samplers?.[0]?.interpolation !== "STEP") {
    throw new Error("The surface animation is not STEP-interpolated.");
  }
  const framesRaw = (extras.frames as Record<string, number>[] | undefined) ?? [];
  const von = (extras.von_mises_range as number[] | undefined) ?? [0, 1];
  const strain = (extras.plastic_strain_range as number[] | null) ?? null;
  const strainNames = (extras.plastic_strain_attributes as string[] | undefined) ?? [];
  return {
    format: String(extras.format),
    vertexCount: Number(extras.n_vertices ?? 0),
    triangleCount: Number(extras.n_triangles ?? 0),
    frameCount: Number(extras.n_frames ?? framesRaw.length),
    times: ((extras.times as number[] | undefined) ?? []).map(Number),
    frames: framesRaw.map((frame) => ({
      index: Number(frame.index),
      animTime: Number(frame.anim_time),
      loadTime: Number(frame.load_time),
      loadN: frame.load_N == null ? null : Number(frame.load_N),
      eroded: Number(frame.eroded ?? 0),
    })),
    timeAxis: String(extras.time_axis ?? "time"),
    lengthUnit: String(extras.length_unit ?? "m"),
    upAxis: String(extras.up_axis ?? "Y"),
    sourceToGltf: String(extras.source_to_gltf ?? "(x,z,-y)*scale"),
    scale: Number(extras.scale ?? 1),
    vonMisesMin: Number(von[0]),
    vonMisesMax: Number(von[1]),
    plasticStrainMin: strain ? Number(strain[0]) : 0,
    plasticStrainMax: strain ? Number(strain[1]) : 1,
    hasPlasticStrain: strainNames.length > 0,
    stressUnit: String(extras.stress_unit ?? "Pa"),
    physicalUntil: extras.physical_until_s == null ? null : Number(extras.physical_until_s),
  };
}

export function frameAtTime(time: number, times: number[]): number {
  let index = 0;
  for (let i = 0; i < times.length; i++) {
    if (times[i] <= time + 1e-9) index = i;
  }
  return index;
}

/**
 * Frame the viewer opens on: the peak-load frame (first frame at the largest |load_N|, ignoring frames
 * after physicalUntil), else the frame with the largest von Mises value (vmPeaks[k] = max over frame k),
 * else the last frame.
 */
export function defaultFrame(contract: Pick<SurfaceContract, "frames" | "frameCount" | "physicalUntil">, vmPeaks?: number[] | null): number {
  const count = Math.max(contract.frameCount, 0);
  if (count === 0) return 0;
  const physical = (index: number) => {
    const frame = contract.frames[index];
    return contract.physicalUntil == null || !frame || frame.loadTime <= contract.physicalUntil + 1e-9;
  };
  let best = -1;
  let bestLoad = 0;
  for (let i = 0; i < Math.min(count, contract.frames.length); i++) {
    const load = contract.frames[i].loadN;
    if (load == null || !Number.isFinite(load) || !physical(i)) continue;
    if (Math.abs(load) > bestLoad + 1e-12) {
      bestLoad = Math.abs(load);
      best = i;
    }
  }
  if (best >= 0) return best;
  if (vmPeaks && vmPeaks.length) {
    let peak = -Infinity;
    for (let i = 0; i < Math.min(count, vmPeaks.length); i++) {
      if (!physical(i)) continue;
      if (vmPeaks[i] > peak) {
        peak = vmPeaks[i];
        best = i;
      }
    }
    if (best >= 0) return best;
  }
  return count - 1;
}
