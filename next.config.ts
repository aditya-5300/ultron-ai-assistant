import type { NextConfig } from "next";

const nextConfig: NextConfig = { serverExternalPackages: ["playwright-core", "pdf-parse", "mammoth"] };

export default nextConfig;
