import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { readObject } from "@/lib/s3";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id } = await context.params;
  const owned = await prisma.simulation.findFirst({ where: { id, userId: user.sub }, select: { id: true } });
  if (!owned) return NextResponse.json({ error: "Simulation not found" }, { status: 404 });
  const artifact = await prisma.artifact.findFirst({
    where: { simulationId: id, s3Key: { endsWith: "vtk/frames.json" } },
  });
  if (!artifact) return NextResponse.json({ error: "frames.json is not ready yet." }, { status: 404 });
  try {
    const object = await readObject(artifact.s3Key);
    return new Response(Buffer.from(object.bytes), {
      headers: { "Content-Type": "application/json", "Cache-Control": "private, max-age=30" },
    });
  } catch {
    return NextResponse.json({ error: "Could not read frames.json from object storage." }, { status: 502 });
  }
}
