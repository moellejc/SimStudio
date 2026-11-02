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
  const artifacts = await prisma.artifact.findMany({ where: { simulationId: id } });
  const artifact =
    artifacts.find((item) => item.kind.startsWith("gltf/") && item.filename.endsWith(".glb")) ??
    artifacts.find((item) => item.filename.endsWith(".glb") && item.filename !== "model.glb");
  if (!artifact) return NextResponse.json({ error: "No glTF surface has been exported for this run." }, { status: 404 });
  try {
    const object = await readObject(artifact.s3Key);
    return new Response(Buffer.from(object.bytes), {
      headers: {
        "Content-Type": "model/gltf-binary",
        "Content-Disposition": `inline; filename="${artifact.filename}"`,
        "Cache-Control": "private, max-age=60",
      },
    });
  } catch {
    return NextResponse.json({ error: "Could not read the glTF model from object storage." }, { status: 502 });
  }
}
