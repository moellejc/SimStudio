import bcrypt from "bcryptjs";
import { NextResponse } from "next/server";
import { COOKIE, cookieOptions, signSession } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { loginSchema } from "@/lib/validators";

export async function POST(request: Request) {
  const parsed = loginSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Email and password are required." }, { status: 400 });
  const user = await prisma.user.findUnique({ where: { email: parsed.data.email.toLowerCase() } });
  if (!user || !(await bcrypt.compare(parsed.data.password, user.passwordHash))) {
    return NextResponse.json({ error: "Email or password is wrong." }, { status: 401 });
  }
  const token = await signSession({ sub: user.id, email: user.email, name: user.name });
  const response = NextResponse.json({ id: user.id, email: user.email, name: user.name });
  response.cookies.set(COOKIE, token, cookieOptions());
  return response;
}
