import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'node:path';
import fs from 'node:fs';

/** 저장소 루트의 .env 를 읽는다 (자격증명이 아닌 포트·도메인만 사용) */
function rootEnv(): Record<string, string> {
  const out: Record<string, string> = {};
  try {
    for (const line of fs.readFileSync(path.resolve(__dirname, '..', '.env'), 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  } catch { /* 없으면 기본값 */ }
  return out;
}

export default defineConfig(() => {
  const e = rootEnv();
  const FRONT = Number(e.FRONTEND_PORT ?? 9502);
  const BACK = Number(e.BACKEND_PORT ?? 9522);
  const DOMAIN = e.PUBLIC_DOMAIN ?? 'localhost';
  return {
    plugins: [vue()],
    server: {
      host: '0.0.0.0',
      port: FRONT,
      strictPort: true,
      allowedHosts: [DOMAIN, `.${DOMAIN.split('.').slice(-2).join('.')}`, 'localhost'],
      proxy: { '/api': { target: `http://127.0.0.1:${BACK}`, changeOrigin: true } },
    },
    preview: {
      host: '0.0.0.0',
      port: FRONT,
      strictPort: true,
      allowedHosts: [DOMAIN, `.${DOMAIN.split('.').slice(-2).join('.')}`, 'localhost'],
      proxy: { '/api': { target: `http://127.0.0.1:${BACK}`, changeOrigin: true } },
    },
    resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
    build: { outDir: 'dist', sourcemap: false },
  };
});
