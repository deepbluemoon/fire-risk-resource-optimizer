import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

/** .env 로더 — 자격증명은 저장소에 커밋하지 않는다 */
function loadEnv(): void {
  try {
    for (const line of fs.readFileSync(path.join(ROOT, '.env'), 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  } catch { /* .env 없으면 환경변수만 사용 */ }
}
loadEnv();

/** 관할 시군구 수 — "항상 이 행수를 낸다" 는 계약(SC-006·FR-014)의 기준값.
 *
 *  2026-09-04 에 251 → 250 이 됐다. 군위군이 2023-07 대구 편입으로 경북·대구
 *  두 행으로 중복돼 있었고 그중 하나가 참조 테이블에서 빠졌다. 그날의 장애는
 *  이 숫자가 ml/src/fire_ml/config.py · check_project.sh · 계약 테스트에
 *  **각각 따로 박혀 있어서** 한 곳만 고쳐도 나머지가 배치를 멈춰 세운 것이다.
 *  Node 쪽 기준점은 여기 하나로 모은다. 파이썬 쪽 N_REGIONS 와는 값이 같아야 한다. */
export const REGION_COUNT = Number(process.env.REGION_COUNT ?? 250);

export const config = {
  root: ROOT,
  regionCount: REGION_COUNT,
  port: Number(process.env.BACKEND_PORT ?? 9522),
  host: process.env.BACKEND_HOST ?? '0.0.0.0',
  publicDomain: process.env.PUBLIC_DOMAIN ?? 'localhost',
  modelVersion: process.env.MODEL_VERSION ?? 'm0-lookup-20260901',
  db: {
    host: process.env.FIRE_DB_HOST!,
    port: Number(process.env.FIRE_DB_PORT ?? 13306),
    user: process.env.FIRE_DB_USER!,
    password: process.env.FIRE_DB_PASS!,
    database: process.env.FIRE_DB_NAME!,
  },
};

if (!config.db.host || !config.db.password) {
  throw new Error('DB 접속정보가 없습니다. .env 를 만들거나 FIRE_DB_* 환경변수를 설정하세요 (.env.example 참조).');
}
