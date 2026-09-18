/** 서비스의 기준 시간대 — 대한민국 서울. */
export const TZ = 'Asia/Seoul';

/** 서울 기준 YYYY-MM-DD.
 *
 *  new Date().toISOString() 은 UTC 를 돌려준다. 서울은 UTC+9 이므로 00~09 시 사이에는
 *  '어제' 가 나온다. 그 값을 기본 조회일로 쓰면 매일 아침 9 시간 동안 하루 전 예측이
 *  화면에 뜬다. 날짜를 만들 때는 반드시 이 함수를 쓴다. (en-CA 로케일이 이 형식을 준다) */
export function todaySeoul(d: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(d);
}

/** 달력상 실재하는 날짜인지까지 검증한다.
 *  형식만 보면 2026-13-99 가 통과해 조회가 0행을 내고, "251행 보장" 계약이 조용히 깨진다. */
export function isValidDate(s: unknown): s is string {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = s.split('-').map(Number);
  if (m < 1 || m > 12 || d < 1 || d > 31) return false;
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}
