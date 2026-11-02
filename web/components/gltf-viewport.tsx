"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type ComponentRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { fitCamera, objectBox } from "@/lib/fit-camera";
import { frameAtTime, jet, parseSurfaceContract, type SurfaceContract } from "@/lib/glb-contract";

type Field = "stress" | "strain";

const TIME_SCALE = 0.04;

export function GltfViewport({
  url,
  partUrl,
  showPart,
  frame,
  playing,
  field,
  onContract,
  onFrame,
  onPlaybackEnd,
}: {
  url: string;
  partUrl: string | null;
  showPart: boolean;
  frame: number;
  playing: boolean;
  field: Field;
  onContract: (contract: SurfaceContract) => void;
  onFrame: (frame: number) => void;
  onPlaybackEnd: () => void;
}) {
  const [buffer, setBuffer] = useState<ArrayBuffer | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setBuffer(null);
    setError(null);
    fetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "No glTF surface for this run.");
        return response.arrayBuffer();
      })
      .then((body) => {
        if (!cancelled) setBuffer(body);
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (error) return <p className="p-6 text-sm text-[#5c5348]">{error}</p>;
  if (!buffer) return <p className="p-6 text-sm text-[#5c5348]">Loading the surface…</p>;
  return (
    <Canvas camera={{ position: [0.3, 0.2, 0.4], fov: 35, near: 1e-4, far: 10 }} gl={{ antialias: true }}>
      <color attach="background" args={["#ffffff"]} />
      <hemisphereLight args={["#ffffff", "#888888", 1]} />
      <Surface
        buffer={buffer}
        partUrl={partUrl}
        showPart={showPart}
        frame={frame}
        playing={playing}
        field={field}
        onContract={onContract}
        onFrame={onFrame}
        onPlaybackEnd={onPlaybackEnd}
      />
    </Canvas>
  );
}

function Surface({
  buffer,
  partUrl,
  showPart,
  frame,
  playing,
  field,
  onContract,
  onFrame,
  onPlaybackEnd,
}: {
  buffer: ArrayBuffer;
  partUrl: string | null;
  showPart: boolean;
  frame: number;
  playing: boolean;
  field: Field;
  onContract: (contract: SurfaceContract) => void;
  onFrame: (frame: number) => void;
  onPlaybackEnd: () => void;
}) {
  const contract = useMemo(() => parseSurfaceContract(buffer), [buffer]);
  const camera = useThree((state) => state.camera) as THREE.PerspectiveCamera;
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null);
  const [result, setResult] = useState<THREE.Group | null>(null);
  const [part, setPart] = useState<THREE.Group | null>(null);
  const mixer = useRef<THREE.AnimationMixer | null>(null);
  const action = useRef<THREE.AnimationAction | null>(null);
  const painted = useRef(-1);
  const fieldRef = useRef(field);
  const playingRef = useRef(playing);
  fieldRef.current = field;
  playingRef.current = playing;

  useEffect(() => {
    onContract(contract);
  }, [contract, onContract]);

  useEffect(() => {
    const loader = new GLTFLoader();
    let alive = true;
    loader.parse(buffer.slice(0), "", (gltf) => {
      if (!alive) return;
      const clip = gltf.animations[0];
      const nextMixer = new THREE.AnimationMixer(gltf.scene);
      const nextAction = nextMixer.clipAction(clip);
      nextAction.setLoop(THREE.LoopOnce, 1);
      nextAction.clampWhenFinished = true;
      nextAction.timeScale = TIME_SCALE;
      nextAction.play();
      nextMixer.setTime(contract.times[0] ?? 0);
      nextAction.paused = true;
      nextMixer.addEventListener("finished", () => {
        if (playingRef.current) onPlaybackEnd();
      });
      gltf.scene.traverse((object) => {
        const mesh = object as THREE.Mesh;
        if (!mesh.isMesh) return;
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const material of materials) {
          const standard = material as THREE.MeshStandardMaterial;
          standard.vertexColors = true;
          if (!mesh.geometry.getAttribute("normal")) standard.flatShading = true;
          standard.needsUpdate = true;
        }
        paint(mesh.geometry, fieldRef.current, 0, contract);
      });
      mixer.current = nextMixer;
      action.current = nextAction;
      painted.current = 0;
      setResult(gltf.scene);
    });
    return () => {
      alive = false;
      mixer.current?.stopAllAction();
      mixer.current = null;
      action.current = null;
    };
  }, [buffer, contract, onPlaybackEnd]);

  useEffect(() => {
    if (!partUrl || !showPart) return;
    let alive = true;
    fetch(partUrl)
      .then(async (response) => {
        if (!response.ok) throw new Error("part");
        return response.arrayBuffer();
      })
      .then((body) => {
        if (!alive) return;
        new GLTFLoader().parse(body, "", (gltf) => {
          if (!alive) return;
          gltf.scene.traverse((object) => {
            const mesh = object as THREE.Mesh;
            if (!mesh.isMesh) return;
            const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
            for (const material of materials) {
              const standard = material as THREE.MeshStandardMaterial;
              standard.transparent = true;
              standard.opacity = 0.28;
              standard.depthWrite = false;
              standard.vertexColors = false;
              standard.color = new THREE.Color("#6b7280");
            }
          });
          setPart(gltf.scene);
        });
      })
      .catch(() => setPart(null));
    return () => {
      alive = false;
    };
  }, [partUrl, showPart]);

  useEffect(() => {
    if (!result || playing) return;
    const clip = action.current;
    const mix = mixer.current;
    if (!clip || !mix) return;
    const index = Math.min(frame, contract.frameCount - 1);
    clip.reset();
    clip.play();
    mix.setTime(contract.times[index] ?? 0);
    clip.paused = true;
    result.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (mesh.isMesh) paint(mesh.geometry, field, index, contract);
    });
    painted.current = index;
  }, [result, frame, playing, field, contract]);

  useEffect(() => {
    const clip = action.current;
    if (!clip || !result) return;
    clip.timeScale = TIME_SCALE;
    if (!playing) {
      clip.paused = true;
      return;
    }
    const end = contract.times[contract.frameCount - 1] ?? 0;
    if ((mixer.current?.time ?? 0) >= end - 1e-6) {
      clip.reset();
      mixer.current?.setTime(contract.times[0] ?? 0);
      painted.current = -1;
    }
    clip.paused = false;
    clip.play();
  }, [playing, result, contract]);

  useEffect(() => {
    if (!result || painted.current < 0) return;
    result.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (mesh.isMesh) paint(mesh.geometry, field, painted.current, contract);
    });
  }, [field, result, contract]);

  useFrame((_, delta) => {
    const mix = mixer.current;
    if (!mix || !playingRef.current || !result) return;
    const before = mix.time;
    mix.update(delta);
    if (mix.time === before && (action.current?.paused ?? false)) return;
    const index = frameAtTime(mix.time, contract.times);
    if (index !== painted.current) {
      result.traverse((object) => {
        const mesh = object as THREE.Mesh;
        if (mesh.isMesh) paint(mesh.geometry, fieldRef.current, index, contract);
      });
      painted.current = index;
      onFrame(index);
    }
  });

  useEffect(() => {
    if (!result) return;
    const box = objectBox(result);
    if (showPart && part) box.union(objectBox(part));
    fitCamera(camera, controls.current, box);
  }, [result, part, showPart, camera]);

  return (
    <>
      <group>
        {result ? <primitive object={result} /> : null}
        {part ? <primitive object={part} visible={showPart} /> : null}
      </group>
      <OrbitControls ref={controls} makeDefault />
    </>
  );
}

function paint(geometry: THREE.BufferGeometry, field: Field, frame: number, contract: SurfaceContract) {
  const color = geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
  const name = `${field === "strain" ? "_plastic_strain_" : "_von_mises_"}${String(frame).padStart(4, "0")}`;
  const values = geometry.getAttribute(name) as THREE.BufferAttribute | undefined;
  if (!color || !values) return;
  const lo = field === "strain" ? contract.plasticStrainMin : contract.vonMisesMin;
  const hi = field === "strain" ? contract.plasticStrainMax : contract.vonMisesMax;
  const span = hi - lo || 1;
  for (let i = 0; i < values.count; i++) {
    const [r, g, b] = jet((values.getX(i) - lo) / span);
    color.setXYZ(i, r, g, b);
  }
  color.needsUpdate = true;
}
