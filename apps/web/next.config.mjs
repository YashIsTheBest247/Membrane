/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  env: {
    // Read at build time for the browser; the server routes read process.env
    // directly so a container can be re-pointed without rebuilding.
    NEXT_PUBLIC_MEMBRANE_API:
      process.env.NEXT_PUBLIC_MEMBRANE_API ?? "http://localhost:8080",
  },
};
export default nextConfig;
