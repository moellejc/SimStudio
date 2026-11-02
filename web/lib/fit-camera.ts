import * as THREE from "three";

export function morphedBox(geometry: THREE.BufferGeometry): THREE.Box3 {
  const position = geometry.getAttribute("position");
  const targets = geometry.morphAttributes.position ?? [];
  const box = new THREE.Box3();
  const point = new THREE.Vector3();
  const passes = Math.max(targets.length, 1);
  for (let frame = 0; frame < passes; frame++) {
    const delta = targets[frame];
    for (let i = 0; i < position.count; i++) {
      point.set(position.getX(i), position.getY(i), position.getZ(i));
      if (delta) point.x += delta.getX(i);
      if (delta) point.y += delta.getY(i);
      if (delta) point.z += delta.getZ(i);
      box.expandByPoint(point);
    }
  }
  return box;
}

export function objectBox(root: THREE.Object3D): THREE.Box3 {
  const box = new THREE.Box3();
  root.updateWorldMatrix(true, true);
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    box.union(morphedBox(mesh.geometry).applyMatrix4(mesh.matrixWorld));
  });
  return box;
}

type Orbit = { target: THREE.Vector3; update: () => void };

export function fitCamera(camera: THREE.PerspectiveCamera, controls: Orbit | null, box: THREE.Box3) {
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length(), 1e-4);
  camera.up.set(0, 1, 0);
  camera.position.set(center.x + 0.9 * radius, center.y + 0.6 * radius, center.z + 1.1 * radius);
  camera.near = radius / 200;
  camera.far = radius * 80;
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  if (controls) {
    controls.target.copy(center);
    controls.update();
  }
}
