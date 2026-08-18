import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next",
  output: "standalone",
  devIndicators: false,
  turbopack: {
    root: frontendRoot
  },
  images: {
    unoptimized: true
  },
  async redirects() {
    return [
      {
        source: "/",
        destination: "/path",
        permanent: false
      }
    ];
  }
};

export default nextConfig;
