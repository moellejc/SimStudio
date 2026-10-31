export function ForceChart({ times, forces }: { times: number[]; forces: number[] }) {
  if (!times?.length || !forces?.length) {
    return <p className="text-sm text-[#5c5348]">No wall-force samples in this summary.</p>;
  }
  const n = Math.min(times.length, forces.length);
  const xs = times.slice(0, n);
  const ys = forces.slice(0, n).map((value) => Math.abs(value));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const maxY = Math.max(...ys, 1e-6);
  const w = 640;
  const h = 180;
  const pad = 28;
  const points = xs
    .map((x, i) => {
      const px = pad + ((x - minX) / Math.max(maxX - minX, 1e-9)) * (w - pad * 2);
      const py = h - pad - (ys[i] / maxY) * (h - pad * 2);
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-48 w-full rounded-lg bg-white">
      <polyline fill="none" stroke="#9a3412" strokeWidth="2" points={points} />
      <text x={pad} y={16} fontSize="11" fill="#5c5348">
        wall force {maxY.toFixed(2)} N
      </text>
      <text x={w - pad} y={h - 8} fontSize="11" fill="#5c5348" textAnchor="end">
        {maxX.toFixed(1)} ms
      </text>
    </svg>
  );
}
