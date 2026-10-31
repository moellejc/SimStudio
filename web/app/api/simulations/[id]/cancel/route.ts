import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { cancelSim } from "@/lib/grpc";

export async function POST(_request: Request, context: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id } = await context.params;
  const simulation = await prisma.simulation.findFirst({ where: { id, userId: user.sub } });
  if (!simulation) return NextResponse.json({ error: "Simulation not found" }, { status: 404 });
  try {
    await cancelSim(id, user.sub);
  } catch (error) {
    const message = error instanceof Error ? error.message : "cancel failed";
    return NextResponse.json({ error: message }, { status: 503 });
  }
  return NextResponse.json({ ok: true });
}
