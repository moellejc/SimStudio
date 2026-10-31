/** Display helpers. Fork decks are kg/mm/ms: stress × 1e9 = Pa, force × 1000 = N. */

export function formatForceN(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 100) return `${value.toFixed(0)} N`;
  if (Math.abs(value) >= 10) return `${value.toFixed(1)} N`;
  return `${value.toFixed(2)} N`;
}

export function formatPa(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toExponential(2)} Pa`;
}

export function formatStrain(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toExponential(2);
}

export function formatPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(2)}%`;
}

export function statusLabel(status: string) {
  return (
    {
      queued: "Queued",
      meshing: "Meshing",
      running_starter: "Starter",
      running_engine: "Engine",
      postprocessing: "Post",
      succeeded: "Finished",
      failed: "Failed",
      cancelled: "Cancelled",
    }[status] || status
  );
}

export function isActive(status: string) {
  return ["queued", "meshing", "running_starter", "running_engine", "postprocessing"].includes(status);
}
