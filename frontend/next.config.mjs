/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
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
