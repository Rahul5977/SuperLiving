import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // Enable image optimization for S3
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "your-superliving-bucket.s3.amazonaws.com",
      },
    ],
  },

  // CORS headers for API calls
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Access-Control-Allow-Credentials",
            value: "true",
          },
          {
            key: "Access-Control-Allow-Origin",
            value: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
          },
        ],
      },
    ];
  },
  // Optimize for production
  compress: true,
  productionBrowserSourceMaps: false,
};

export default nextConfig;
