"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useRef, useState, type ComponentRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { fitCamera, objectBox } from "@/lib/fit-camera";

export function PartViewport({ url }: { url: string }) {
  const [buffer, setBuffer] = useState<ArrayBuffer | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error("The part preview is not available.");
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
  if (!buffer) return <p className="p-6 text-sm text-[#5c5348]">Loading the part…</p>;
  return (
    <Canvas camera={{ position: [0.3, 0.2, 0.4], fov: 35, near: 1e-4, far: 10 }} gl={{ antialias: true }}>
      <color attach="background" args={["#ffffff"]} />
      <ambientLight intensity={0.85} />
      <directionalLight position={[1, 2, 1]} intensity={0.8} />
      <Part buffer={buffer} />
    </Canvas>
  );
}

function Part({ buffer }: { buffer: ArrayBuffer }) {
  const camera = useThree((state) => state.camera) as THREE.PerspectiveCamera;
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null);
  const [model, setModel] = useState<THREE.Group | null>(null);

  useEffect(() => {
    const loader = new GLTFLoader();
    let alive = true;
    loader.parse(buffer.slice(0), "", (gltf) => {
      if (!alive) return;
      setModel(gltf.scene);
    });
    return () => {
      alive = false;
    };
  }, [buffer]);

  useEffect(() => {
    if (!model) return;
    fitCamera(camera, controls.current, objectBox(model));
  }, [model, camera]);

  return (
    <>
      {model ? <primitive object={model} /> : null}
      <OrbitControls ref={controls} makeDefault />
    </>
  );
}
