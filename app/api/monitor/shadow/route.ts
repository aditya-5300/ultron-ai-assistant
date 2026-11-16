import { handleShadow } from "@/lib/health/shadow/observe.cjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export function GET(request: Request) {
  return handleShadow(request);
}
