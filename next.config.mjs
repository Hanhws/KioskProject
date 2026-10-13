/** @type {import('next').NextConfig} */
const nextConfig = {
  // 개발 중에는 /api/* 를 로컬 FastAPI(8000)로, Vercel 배포에서는 api/index.py 함수로 보낸다.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination:
          process.env.NODE_ENV === "development" ? "http://127.0.0.1:8000/api/:path*" : "/api/",
      },
    ];
  },
};

export default nextConfig;
