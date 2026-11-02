import { z } from "zod";

export const registerSchema = z.object({
  name: z.string().trim().min(1).max(80),
  email: z.string().trim().email().max(200),
  password: z.string().min(8).max(200),
});

export const loginSchema = z.object({
  email: z.string().trim().email(),
  password: z.string().min(1),
});

export const templateSchema = z.object({
  name: z.string().trim().min(1).max(80),
  templateId: z.enum(["fork_load", "fork_displacement"]),
  material: z.enum(["steel", "aluminum", "plastic"]),
  dryRun: z.boolean().default(true),
  endTimeMs: z.number().positive().max(500),
  loadKN: z.number().positive().max(50),
  stopDisplacementMm: z.number().positive().max(80),
  pushVelocityMmMs: z.number().positive().max(20),
  rampMs: z.number().positive().max(500),
  threads: z.number().int().min(1).max(16),
  friction: z.number().min(0).max(1),
});

export type TemplateInput = z.infer<typeof templateSchema>;

export function deckNamesOk(starter: string, engine: string) {
  const stem = (name: string, which: "0000" | "0001") => {
    const base = name.split(/[/\\]/).pop() || "";
    const match = base.match(/^([A-Za-z0-9_-]+)_000[01]\.rad$/i);
    if (!match) return null;
    if (!base.toLowerCase().endsWith(`_${which}.rad`)) return null;
    return match[1];
  };
  const left = stem(starter, "0000");
  const right = stem(engine, "0001");
  if (!left || !right || left.toLowerCase() !== right.toLowerCase()) return null;
  return left;
}
