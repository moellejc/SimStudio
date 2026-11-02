import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { submitSim } from "@/lib/grpc";
import { deckNamesOk, templateSchema } from "@/lib/validators";

export async function GET() {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const simulations = await prisma.simulation.findMany({
    where: { userId: user.sub },
    orderBy: { createdAt: "desc" },
    take: 100,
  });
  return NextResponse.json({ simulations });
}

export async function POST(request: Request) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const contentType = request.headers.get("content-type") || "";
  if (contentType.includes("multipart/form-data")) return createUpload(request, user.sub);
  return createTemplate(request, user.sub);
}

async function createTemplate(request: Request, userId: string) {
  const parsed = templateSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Check the study fields and try again." }, { status: 400 });
  const data = parsed.data;
  const simulation = await prisma.simulation.create({
    data: {
      userId,
      name: data.name,
      sourceType: "template",
      templateId: data.templateId,
      material: data.material,
      dryRun: data.dryRun,
      parameters: data,
      status: "queued",
      message: "Queued",
    },
  });
  try {
    await submitSim({
      simulationId: simulation.id,
      userId,
      name: data.name,
      dryRun: data.dryRun,
      template: {
        templateId: data.templateId,
        material: data.material,
        endTimeMs: data.endTimeMs,
        loadKN: data.loadKN,
        stopDisplacementMm: data.stopDisplacementMm,
        pushVelocityMmMs: data.pushVelocityMmMs,
        rampMs: data.rampMs,
        threads: data.threads,
        friction: data.friction,
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Simulation service unavailable";
    await prisma.simulation.update({
      where: { id: simulation.id },
      data: { status: "failed", errorMessage: message, message: "Could not reach the sim server" },
    });
    return NextResponse.json({ error: "The simulation service did not accept the job.", id: simulation.id }, { status: 503 });
  }
  return NextResponse.json({ simulation }, { status: 201 });
}

async function createUpload(request: Request, userId: string) {
  const form = await request.formData();
  const name = String(form.get("name") || "").trim();
  const dryRun = form.get("dryRun") != null;
  const starter = form.get("starter");
  const engine = form.get("engine");
  const threadsRaw = form.get("threads");
  const threads = threadsRaw == null || threadsRaw === "" ? 0 : Number(threadsRaw);
  if (!Number.isInteger(threads) || threads < 0 || threads > 16) {
    return NextResponse.json({ error: "Threads must be a whole number from 1 to 16." }, { status: 400 });
  }
  if (!name || !(starter instanceof File) || !(engine instanceof File)) {
    return NextResponse.json({ error: "Name, starter deck, and engine deck are required." }, { status: 400 });
  }
  const run = deckNamesOk(starter.name, engine.name);
  if (!run) return NextResponse.json({ error: "Use matching RunName_0000.rad and RunName_0001.rad files." }, { status: 400 });
  if (starter.size > 8_000_000 || engine.size > 8_000_000) {
    return NextResponse.json({ error: "Each deck must be under 8 MB." }, { status: 400 });
  }
  const simulation = await prisma.simulation.create({
    data: {
      userId,
      name,
      sourceType: "upload",
      templateId: "upload",
      dryRun,
      parameters: { runName: run, threads: threads || null },
      status: "queued",
      message: "Queued",
    },
  });
  try {
    await submitSim({
      simulationId: simulation.id,
      userId,
      name,
      dryRun,
      deck: {
        starterFilename: starter.name,
        starterDeck: new Uint8Array(await starter.arrayBuffer()),
        engineFilename: engine.name,
        engineDeck: new Uint8Array(await engine.arrayBuffer()),
        threads,
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Simulation service unavailable";
    await prisma.simulation.update({
      where: { id: simulation.id },
      data: { status: "failed", errorMessage: message, message: "Could not reach the sim server" },
    });
    return NextResponse.json({ error: "The simulation service did not accept the decks.", id: simulation.id }, { status: 503 });
  }
  return NextResponse.json({ simulation }, { status: 201 });
}
