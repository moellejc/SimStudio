import { NextResponse } from "next/server";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { readObject } from "@/lib/s3";

export async function GET(request: Request, context: { params: Promise<{ id: string; artifactId: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id, artifactId } = await context.params;
  const inline = new URL(request.url).searchParams.get("inline") === "1";
  const artifact = await prisma.artifact.findFirst({
    where: { id: artifactId, simulationId: id, simulation: { userId: user.sub } },
  });
  if (!artifact) return NextResponse.json({ error: "Artifact not found" }, { status: 404 });
  try {
    const object = await readObject(artifact.s3Key);
    return new Response(Buffer.from(object.bytes), {
      headers: {
        "Content-Type": artifact.contentType || object.contentType,
        "Content-Disposition": `${inline ? "inline" : "attachment"}; filename="${artifact.filename}"`,
      },
    });
  } catch {
    return NextResponse.json({ error: "Object storage did not return this file." }, { status: 502 });
  }
}
