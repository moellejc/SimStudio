import { readFileSync } from "node:fs";
import path from "node:path";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { fitCamera, fitDistance, morphedBox } from "../lib/fit-camera";
import { defaultFrame, parseSurfaceContract, type SurfaceContract } from "../lib/glb-contract";

function frames(loads: (number | null)[]): SurfaceContract["frames"] {
  return loads.map((loadN, index) => ({ loadN, loadTime: index, animTime: index / 10 })) as SurfaceContract["frames"];
}

describe("default camera fit", () => {
  // A tall, thin part (like the fork) whose top grows in later frames.
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 0.02, 0, 0, 0, 0.3, 0], 3));
  geometry.morphAttributes.position = [
    new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, 0, 0, 0, 0], 3),
    new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, 0, 0.05, 0.1, 0], 3),
  ];
  geometry.morphTargetsRelative = true;

  it("includes every frame's extent in the box", () => {
    const box = morphedBox(geometry);
    expect(box.max.y).toBeCloseTo(0.4);
    expect(box.max.x).toBeCloseTo(0.05);
  });

  it("puts every box corner inside the view frustum with margin", () => {
    const box = morphedBox(geometry);
    for (const aspect of [0.6, 1, 2.5]) {
      const camera = new THREE.PerspectiveCamera(35, aspect, 1e-4, 10);
      fitCamera(camera, null, box);
      camera.updateMatrixWorld();
      const frustum = new THREE.Frustum().setFromProjectionMatrix(
        new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse),
      );
      for (const x of [box.min.x, box.max.x])
        for (const y of [box.min.y, box.max.y])
          for (const z of [box.min.z, box.max.z]) {
            const corner = new THREE.Vector3(x, y, z);
            expect(frustum.containsPoint(corner)).toBe(true);
            const ndc = corner.clone().project(camera);
            expect(Math.abs(ndc.x)).toBeLessThan(0.95);
            expect(Math.abs(ndc.y)).toBeLessThan(0.95);
          }
    }
  });

  it("backs off further for narrow viewports", () => {
    const box = morphedBox(geometry);
    expect(fitDistance(box, 35, 0.5)).toBeGreaterThan(fitDistance(box, 35, 2));
    expect(fitDistance(box, 35, 2, 1.3)).toBeGreaterThan(fitDistance(box, 35, 2, 1));
  });
});

describe("default frame", () => {
  it("opens on the first peak-load frame", () => {
    expect(defaultFrame({ frames: frames([0, 10, 40, 40, 20]), frameCount: 5, physicalUntil: null })).toBe(2);
  });

  it("ignores frames after the physical cut-off", () => {
    expect(defaultFrame({ frames: frames([0, 10, 30, 90]), frameCount: 4, physicalUntil: 2 })).toBe(2);
  });

  it("falls back to the max von Mises frame without load data", () => {
    expect(defaultFrame({ frames: frames([null, null, null]), frameCount: 3, physicalUntil: null }, [1, 7, 3])).toBe(1);
  });

  it("falls back to the last frame with nothing to go on", () => {
    expect(defaultFrame({ frames: frames([null, null]), frameCount: 2, physicalUntil: null })).toBe(1);
  });

  it("picks a valid frame for the sample export", () => {
    const file = path.resolve(__dirname, "../../sim-server/post/samples/sample.glb");
    const bytes = readFileSync(file);
    const contract = parseSurfaceContract(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    const index = defaultFrame(contract);
    expect(index).toBeGreaterThanOrEqual(0);
    expect(index).toBeLessThan(contract.frameCount);
  });
});
