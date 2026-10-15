/** @type {import('next').NextConfig} */
const nextConfig = {
  // 개발 중에는 /api/* 를 RAG·통합 API 서버(KioskProject의 FastAPI, 8000)로 보낸다.
  // 배포 시 API가 다른 도메인이면 NEXT_PUBLIC_API_BASE_URL을 설정한다.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
  },
};

export default nextConfig;
