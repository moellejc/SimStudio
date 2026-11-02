import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id } = await context.params;
  const simulation = await prisma.simulation.findFirst({
    where: { id, userId: user.sub },
    include: { artifacts: { orderBy: { filename: "asc" } }, metrics: { orderBy: { name: "asc" } } },
  });
  if (!simulation) return NextResponse.json({ error: "Simulation not found" }, { status: 404 });
  return NextResponse.json({ simulation });
}
