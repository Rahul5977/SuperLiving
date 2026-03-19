import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // ── Security headers ────────────────────────────────────────────────────
  async headers() {
    const isDev = process.env.NODE_ENV === "development";
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
          {
            key: "X-XSS-Protection",
            value: "1; mode=block",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          ...(isDev
            ? []
            : [
                {
                  key: "Strict-Transport-Security",
                  value: "max-age=63072000; includeSubDomains; preload",
                },
              ]),
        ],
      },
    ];
  },

  // ── Rewrites: proxy API calls in production ─────────────────────────────
  async rewrites() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/api/:path*`,
      },
    ];
  },

  // ── Images ──────────────────────────────────────────────────────────────
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "your-superliving-bucket.s3.amazonaws.com",
      },
    ],
  },

  // ── Performance ─────────────────────────────────────────────────────────
  compress: true,
  productionBrowserSourceMaps: false,

  // ── Output: standalone for Docker/ECS deployment ────────────────────────
  output: "standalone",
};

export default nextConfig;