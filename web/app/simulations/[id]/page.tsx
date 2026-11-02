import { SimDetail } from "@/components/sim-detail";
import { SiteHeader } from "@/components/site-header";

export default async function SimulationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="min-h-screen">
      <SiteHeader />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <SimDetail id={id} />
      </main>
    </div>
  );
}
