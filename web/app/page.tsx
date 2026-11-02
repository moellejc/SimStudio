import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { Button } from "@/components/ui/button";

export default function HomePage() {
  return (
    <div className="min-h-screen">
      <SiteHeader />
      <main className="mx-auto grid max-w-6xl gap-10 px-4 py-12 md:grid-cols-[1.2fr_0.8fr] md:py-20">
        <section>
          <p className="font-mono text-xs tracking-[0.16em] text-[#9a3412]">EXPLICIT DYNAMICS</p>
          <h1 className="mt-3 max-w-xl text-4xl leading-tight text-[#1c1915] md:text-5xl">
            A fork, a rigid wall, and the energy it takes to buckle.
          </h1>
          <p className="mt-5 max-w-xl text-lg leading-relaxed text-[#3f382f]">
            SimStudio runs OpenRadioss starter and the OpenMP engine on a photo-derived hex mesh. Pick steel,
            6061-T6, or polystyrene. Watch the listing, then scrub von Mises and plastic strain the way the
            offline frame viewer does.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button size="lg" render={<Link href="/signup" />}>
              Open the run sheet
            </Button>
            <Button size="lg" variant="outline" render={<Link href="/login" />}>
              Sign in
            </Button>
          </div>
          <dl className="mt-10 grid max-w-xl grid-cols-3 gap-4 text-sm">
            <div>
              <dt className="font-mono text-xs text-[#5c5348]">Units</dt>
              <dd className="mt-1">kg, mm, ms</dd>
            </div>
            <div>
              <dt className="font-mono text-xs text-[#5c5348]">Stress</dt>
              <dd className="mt-1">GPa × 1e9 = Pa</dd>
            </div>
            <div>
              <dt className="font-mono text-xs text-[#5c5348]">Engine</dt>
              <dd className="mt-1">engine_linux64_gf</dd>
            </div>
          </dl>
        </section>
        <aside className="rounded-xl border border-[#e4d9c8] bg-white p-5 shadow-sm">
          <p className="font-mono text-xs text-[#5c5348]">What a finished study shows</p>
          <ul className="mt-4 space-y-3 text-sm leading-relaxed text-[#1c1915]">
            <li>Peak rigid-wall force, after the cumulative impulse is differentiated into newtons.</li>
            <li>Maximum von Mises stress in pascals, and maximum plastic strain.</li>
            <li>Energy balance error from internal, kinetic, hourglass, and external work.</li>
            <li>A white viewport with a jet scale, playhead, and a floor plane under the tines.</li>
          </ul>
          <p className="mt-5 text-sm text-[#5c5348]">
            Dry run is the default. It writes a solver-shaped listing and a buckled mesh so the stack can be
            reviewed without the binary. Uncheck it only when <span className="font-mono">engine_linux64_gf</span> is
            installed. The MPI build is not used.
          </p>
        </aside>
      </main>
    </div>
  );
}
