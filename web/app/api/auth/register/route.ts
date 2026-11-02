import bcrypt from "bcryptjs";
import { NextResponse } from "next/server";
import { cookieOptions, COOKIE, signSession } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { registerSchema } from "@/lib/validators";

export async function POST(request: Request) {
  const parsed = registerSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Name, email, and a password of at least 8 characters are required." }, { status: 400 });
  const email = parsed.data.email.toLowerCase();
  const existing = await prisma.user.findUnique({ where: { email } });
  if (existing) return NextResponse.json({ error: "An account with that email already exists." }, { status: 409 });
  const user = await prisma.user.create({
    data: { email, name: parsed.data.name, passwordHash: await bcrypt.hash(parsed.data.password, 10) },
  });
  const token = await signSession({ sub: user.id, email: user.email, name: user.name });
  const response = NextResponse.json({ id: user.id, email: user.email, name: user.name });
  response.cookies.set(COOKIE, token, cookieOptions());
  return response;
}
