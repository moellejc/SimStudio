import Redis from "ioredis";
import { isResponse, requireUser } from "@/lib/auth";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  if (isResponse(user)) return user;
  const { id } = await context.params;
  const simulation = await prisma.simulation.findFirst({ where: { id, userId: user.sub } });
  if (!simulation) return new Response("Not found", { status: 404 });
  const redis = new Redis(process.env.REDIS_URL || "redis://127.0.0.1:6379/0");
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (value: unknown) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(value)}\n\n`));
      const snap = await redis.hgetall(`sim:${id}`);
      send({
        type: "status",
        state: snap.state || simulation.status,
        progress: Number(snap.progress || simulation.progress || 0),
        message: snap.message || simulation.message,
        error: snap.error || simulation.errorMessage || "",
      });
      const logs = await redis.lrange(`sim:${id}:logs`, -400, -1);
      for (const line of logs) {
        try {
          send({ type: "log", ...JSON.parse(line) });
        } catch {
          send({ type: "log", stream: "log", line });
        }
      }
      const sub = redis.duplicate();
      await sub.subscribe(`sim:${id}:events`);
      sub.on("message", (_channel, message) => {
        try {
          send(JSON.parse(message));
        } catch {
          /* ignore malformed events */
        }
      });
      const ping = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(": ping\n\n"));
        } catch {
          clearInterval(ping);
        }
      }, 12000);
      request.signal.addEventListener("abort", () => {
        clearInterval(ping);
        sub.disconnect();
        redis.disconnect();
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      });
    },
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
