import { NextResponse, type NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const token = request.cookies.get("radlab_session")?.value;
  const { pathname } = request.nextUrl;
  if (pathname.startsWith("/simulations") && !token) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  if ((pathname === "/login" || pathname === "/signup") && token) {
    return NextResponse.redirect(new URL("/simulations", request.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/simulations/:path*", "/login", "/signup"] };
