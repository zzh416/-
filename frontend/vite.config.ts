import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig(() => {
  // Docker 内由 Compose 注入 VITE_PROXY_TARGET=http://api:9000；
  // 宿主机直接 `npm run dev` 时默认代理到 localhost:9000。
  const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://localhost:9000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
