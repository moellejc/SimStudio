import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { readObject } from "@/lib/s3";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id } = await context.params;
  const artifact = await prisma.artifact.findFirst({
    where: { simulationId: id, kind: "viewer", simulation: { userId: user.sub } },
  });
  if (!artifact) return NextResponse.json({ error: "Viewer frames are not ready yet." }, { status: 404 });
  try {
    const object = await readObject(artifact.s3Key);
    return new Response(Buffer.from(object.bytes), {
      headers: { "Content-Type": "application/json", "Cache-Control": "private, max-age=30" },
    });
  } catch {
    return NextResponse.json({ error: "Could not read viewer frames from object storage." }, { status: 502 });
  }
}
