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

/** View direction of the default camera (from the target towards the camera), before normalising. */
export const VIEW_DIRECTION = new THREE.Vector3(0.9, 0.6, 1.1);

/**
 * Distance from the box centre at which a perspective camera sees the whole box: the box's bounding
 * sphere must fit inside the narrower of the vertical and horizontal fields of view, times `margin`.
 */
export function fitDistance(box: THREE.Box3, fovDeg: number, aspect: number, margin = 1.15): number {
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-4);
  const vHalf = THREE.MathUtils.degToRad(fovDeg) / 2;
  const hHalf = Math.atan(Math.tan(vHalf) * Math.max(aspect, 1e-3));
  const half = Math.min(vHalf, hHalf);
  return (radius / Math.sin(half)) * margin;
}

export function fitCamera(camera: THREE.PerspectiveCamera, controls: Orbit | null, box: THREE.Box3, margin = 1.15) {
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-4);
  const distance = fitDistance(box, camera.fov, camera.aspect, margin);
  camera.up.set(0, 1, 0);
  camera.position.copy(center).addScaledVector(VIEW_DIRECTION.clone().normalize(), distance);
  camera.near = Math.max(distance - radius * 2, distance / 1000);
  camera.far = distance + radius * 4;
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  if (controls) {
    controls.target.copy(center);
    controls.update();
  }
}
