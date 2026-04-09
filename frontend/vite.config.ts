import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_BASE_URL
    ? new URL(env.VITE_API_BASE_URL).origin
    : 'http://localhost:8000'

  return {
    plugins: [
      tailwindcss(),
      react(),
    ],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/ws': {
          target: apiTarget.replace(/^http/, 'ws'),
          ws: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            if (id.includes('node_modules/react') || id.includes('node_modules/react-dom') || id.includes('react-router-dom')) return 'vendor'
            if (id.includes('@tanstack/react-query')) return 'query'
            if (id.includes('zustand')) return 'state'
          },
        },
      },
    },
  }
})
