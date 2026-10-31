import fs from "node:fs";
import path from "node:path";
import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";

type SimClient = grpc.Client & {
  SubmitSim: (req: Record<string, unknown>, cb: (err: grpc.ServiceError | null, res: { message?: string }) => void) => void;
  CancelSim: (req: Record<string, unknown>, cb: (err: grpc.ServiceError | null, res: unknown) => void) => void;
};

let client: SimClient | null = null;

export function protoPath() {
  const candidates = [
    process.env.PROTO_PATH,
    "/proto/simstudio.proto",
    path.resolve(process.cwd(), "proto/simstudio.proto"),
    path.resolve(process.cwd(), "../proto/simstudio.proto"),
    path.resolve(process.cwd(), "../../proto/simstudio.proto"),
  ].filter(Boolean) as string[];
  const found = candidates.find((item) => fs.existsSync(item));
  if (!found) throw new Error(`simstudio.proto not found (looked in ${candidates.join(", ")})`);
  return found;
}

export function simClient() {
  if (client) return client;
  const definition = protoLoader.loadSync(protoPath(), {
    keepCase: false,
    longs: String,
    defaults: true,
    oneofs: true,
  });
  const root = grpc.loadPackageDefinition(definition) as unknown as {
    simstudio: { v1: { SimStudio: new (addr: string, creds: grpc.ChannelCredentials) => SimClient } };
  };
  client = new root.simstudio.v1.SimStudio(process.env.SIM_GRPC_ADDR || "127.0.0.1:50051", grpc.credentials.createInsecure());
  return client;
}

function call<T>(method: "SubmitSim" | "CancelSim", payload: Record<string, unknown>) {
  return new Promise<T>((resolve, reject) => {
    simClient()[method](payload, (err, res) => {
      if (err) reject(err);
      else resolve(res as T);
    });
  });
}

export function submitSim(payload: Record<string, unknown>) {
  return call<{ message?: string }>("SubmitSim", payload);
}

export function cancelSim(simulationId: string, userId: string) {
  return call("CancelSim", { simulationId, userId });
}
