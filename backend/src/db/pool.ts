import mysql from 'mysql2/promise';
import { config } from '../config.js';

export const pool = mysql.createPool({
  ...config.db,
  charset: 'utf8mb4',
  waitForConnections: true,
  connectionLimit: 8,
  // DB(MariaDB)의 time_zone 이 '+09:00' 이고 배치도 KST 로 기록한다. 여기를 'Z'(UTC)로
  // 두면 mysql2 가 KST 값을 UTC 로 읽어 화면 시각이 9시간 앞선다 (13:29 → 22:29).
  timezone: '+09:00',
  dateStrings: ['DATE'],
  decimalNumbers: true,   // DECIMAL 을 문자열이 아닌 number 로 — API 계약을 숫자로 고정
});

/** 원격 DB 라 일시적 연결 끊김(PROTOCOL_CONNECTION_LOST / ECONNRESET)이 발생한다.
 *  읽기 전용 조회이므로 안전하게 재시도한다. */
const TRANSIENT = new Set(['PROTOCOL_CONNECTION_LOST', 'ECONNRESET', 'ETIMEDOUT', 'EPIPE', 'ER_LOCK_DEADLOCK']);

export async function q<T = any>(sql: string, params: any[] = [], retries = 2): Promise<T[]> {
  for (let i = 0; ; i++) {
    try {
      const [rows] = await pool.query(sql, params);
      return rows as T[];
    } catch (e: any) {
      if (i >= retries || !TRANSIENT.has(e?.code)) throw e;
      await new Promise((r) => setTimeout(r, 300 * 2 ** i));
    }
  }
}

export async function q1<T = any>(sql: string, params: any[] = []): Promise<T | null> {
  const rows = await q<T>(sql, params);
  return rows.length ? rows[0] : null;
}

export async function tx<T>(fn: (c: mysql.PoolConnection) => Promise<T>): Promise<T> {
  const c = await pool.getConnection();
  try {
    await c.beginTransaction();
    const out = await fn(c);
    await c.commit();
    return out;
  } catch (e) {
    await c.rollback();
    throw e;
  } finally {
    c.release();
  }
}
