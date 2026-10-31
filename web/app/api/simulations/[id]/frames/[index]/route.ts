import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { readObject } from "@/lib/s3";

export async function GET(_request: Request, context: { params: Promise<{ id: string; index: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id, index } = await context.params;
  const owned = await prisma.simulation.findFirst({ where: { id, userId: user.sub }, select: { id: true } });
  if (!owned) return NextResponse.json({ error: "Simulation not found" }, { status: 404 });
  const n = Number(index);
  if (!Number.isInteger(n) || n < 1) return NextResponse.json({ error: "Bad frame index" }, { status: 400 });
  const name = `frame_${String(n).padStart(3, "0")}.png`;
  const artifact = await prisma.artifact.findFirst({
    where: { simulationId: id, filename: name, s3Key: { contains: "/frames/" } },
  });
  if (!artifact) return NextResponse.json({ error: "Frame image is not ready." }, { status: 404 });
  try {
    const object = await readObject(artifact.s3Key);
    return new Response(Buffer.from(object.bytes), {
      headers: { "Content-Type": "image/png", "Cache-Control": "private, max-age=3600" },
    });
  } catch {
    return NextResponse.json({ error: "Could not read the frame image." }, { status: 502 });
  }
}
