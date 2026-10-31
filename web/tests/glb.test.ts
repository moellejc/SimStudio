import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { frameAtTime, jet, parseSurfaceContract } from "../lib/glb-contract";

describe("sample.glb morph contract", () => {
  const file = path.resolve(__dirname, "../../sim-server/post/samples/sample.glb");
  const bytes = readFileSync(file);
  const contract = parseSurfaceContract(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));

  it("is one Y-up surface with a morph target and stress attribute per frame", () => {
    expect(contract.format).toBe("simstudio-morph-v1");
    expect(contract.frameCount).toBe(26);
    expect(contract.vertexCount).toBe(128);
    expect(contract.frames).toHaveLength(26);
    expect(contract.times).toHaveLength(26);
    expect(contract.lengthUnit).toBe("m");
    expect(contract.upAxis).toBe("Y");
    expect(contract.sourceToGltf).toBe("(x,z,-y)*scale");
    expect(contract.scale).toBe(1);
    expect(contract.vonMisesMax).toBeGreaterThan(contract.vonMisesMin);
    expect(contract.hasPlasticStrain).toBe(true);
    expect(contract.frames[0].animTime).toBe(0);
    expect(contract.frames[25].eroded).toBe(0);
  });

  it("steps the active frame from the animation clock", () => {
    expect(frameAtTime(0, contract.times)).toBe(0);
    expect(frameAtTime(contract.times[12], contract.times)).toBe(12);
    expect(frameAtTime(contract.times[25] + 1, contract.times)).toBe(25);
  });

  it("maps the low end of the jet to dark blue and the high end to dark red", () => {
    expect(jet(0)).toEqual([0, 0, 0.5]);
    expect(jet(1)).toEqual([0.5, 0, 0]);
  });
});
