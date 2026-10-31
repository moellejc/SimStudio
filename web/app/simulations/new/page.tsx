import { CreatePreview } from "@/components/create-preview";
import { NewSimForm } from "@/components/new-sim-form";
import { SiteHeader } from "@/components/site-header";

export default function NewSimulationPage() {
  return (
    <div className="min-h-screen">
      <SiteHeader />
      <main className="mx-auto grid max-w-6xl gap-8 px-4 py-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <h1 className="text-3xl">New study</h1>
          <p className="mt-2 mb-6 text-sm text-[#5c5348]">
            The fork mesh is hexes from the template, tines down on a rigid plane. Force control ramps a cubic load on the handle. Velocity control pushes the handle at a smoothstep speed.
          </p>
          <NewSimForm />
        </div>
        <CreatePreview />
      </main>
    </div>
  );
}
