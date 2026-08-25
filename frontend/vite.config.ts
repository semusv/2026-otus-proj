import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_DEV_PROXY_TARGET ?? 'http://127.0.0.1:8000'

  const proxy = Object.fromEntries(
    ['/api', '/auth', '/admin', '/health'].map((path) => [
      path,
      { target: backendTarget, changeOrigin: true },
    ]),
  )

  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
    test: {
      environment: 'node',
      testTimeout: 5000,
      include: ['src/**/*.test.ts'],
    },
  }
})
