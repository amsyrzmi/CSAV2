import type { NextConfig } from "next";
import { consoleOrigin } from "./lib/upstream";

const nextConfig: NextConfig = {
  outputFileTracingRoot: process.cwd(),
  turbopack: { root: process.cwd() },
  experimental: { proxyClientMaxBodySize: 31 * 1024 * 1024 },
  async rewrites() {
    return [
      {
        source: "/console/v1/:path*",
        destination: `${consoleOrigin()}/console/v1/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
