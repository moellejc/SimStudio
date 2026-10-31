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
  };
}

export function frameAtTime(time: number, times: number[]): number {
  let index = 0;
  for (let i = 0; i < times.length; i++) {
    if (times[i] <= time + 1e-9) index = i;
  }
  return index;
}
