import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { readObject } from "@/lib/s3";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id } = await context.params;
  const artifact = await prisma.artifact.findFirst({
    where: {
      simulationId: id,
      simulation: { userId: user.sub },
      OR: [{ filename: "model.glb" }, { kind: "input/model.glb" }],
    },
  });
  if (!artifact) return NextResponse.json({ error: "This study has no part preview yet." }, { status: 404 });
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
    return NextResponse.json({ error: "Could not read the part from object storage." }, { status: 502 });
  }
}
