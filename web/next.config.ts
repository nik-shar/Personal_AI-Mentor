import type { NextConfig } from "next";

/**
 * The FastAPI backend lives at AI_MENTOR_API (default http://localhost:8000).
 * We proxy /api/* through Next so the browser only ever talks to one origin
 * (no CORS, no baked-in absolute URLs in the client). Override for a remote
 * backend with the AI_MENTOR_API env var.
 */
const API_BASE = process.env.AI_MENTOR_API ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_BASE}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
