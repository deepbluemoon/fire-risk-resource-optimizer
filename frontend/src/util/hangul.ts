/** 한글 지역명 검색 — 초성 입력과 부분 일치를 함께 받는다.
 *
 *  '화성' 처럼 온전한 글자로도, 'ㅎㅅ' 처럼 초성만으로도 찾을 수 있어야 한다.
 *  한글 음절은 U+AC00 부터 (초성 × 588 + 중성 × 28 + 종성) 으로 배열돼 있으므로,
 *  음절 코드를 588 로 나누면 초성 번호가 그대로 나온다.
 */

const CHOSEONG = [
  'ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ',
  'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ',
] as const;

const SYL_FIRST = 0xac00;
const SYL_LAST = 0xd7a3;

/** 쌍자음을 홑자음으로도 찾을 수 있게 한다 — 'ㄱ' 으로 '까치울' 을 놓치지 않는다. */
const RELAX: Record<string, string> = { 'ㄲ': 'ㄱ', 'ㄸ': 'ㄷ', 'ㅃ': 'ㅂ', 'ㅆ': 'ㅅ', 'ㅉ': 'ㅈ' };

/** 음절의 초성. 한글 음절이 아니면 null. */
function choseongOf(ch: string): string | null {
  const c = ch.charCodeAt(0);
  if (c < SYL_FIRST || c > SYL_LAST) return null;
  return CHOSEONG[Math.floor((c - SYL_FIRST) / 588)];
}

/** 자음 하나만 있는 글자인가 — 키보드가 내는 호환 자모(U+3131~U+314E) 기준. */
export function isChoseongChar(ch: string): boolean {
  return (CHOSEONG as readonly string[]).includes(ch);
}

/** 질의가 초성을 하나라도 포함하는가 — 안내 문구를 바꿀 때 쓴다. */
export function hasChoseong(q: string): boolean {
  return [...q].some(isChoseongChar);
}

function sameChar(target: string, key: string): boolean {
  if (target === key) return true;
  if (!isChoseongChar(key)) return false;
  const cho = choseongOf(target);
  if (cho === null) return false;
  if (cho === key) return true;
  // 'ㄱ' 입력으로 'ㄲ' 초성도 잡는다 (반대 방향은 열지 않는다)
  return RELAX[cho] === key;
}

/** text 의 start 위치에서 query 가 통째로 맞아떨어지는가. */
function matchAt(text: string, query: string, start: number): boolean {
  for (let i = 0; i < query.length; i++) {
    const t = text[start + i];
    if (t === undefined || !sameChar(t, query[i])) return false;
  }
  return true;
}

/**
 * 검색 점수. 낮을수록 좋은 일치이고, 안 맞으면 null.
 *
 *   0  이름 전체가 그대로       (화성시 ← '화성시')
 *   1  이름 앞에서부터 맞음     (화성시 ← '화성')
 *   2  이름 가운데에서 맞음     (남양주시 ← '양주')
 *
 * 초성으로 맞은 경우는 같은 자리에서 0.5 를 더해, 글자로 맞은 쪽을 항상 위에 둔다.
 * '성' 처럼 흔한 글자를 넣었을 때 앞글자가 맞는 곳이 먼저 오게 하려는 것이다.
 */
export function scoreMatch(text: string, query: string): number | null {
  if (!query) return 0;
  const t = text.toLowerCase();
  const q = query.toLowerCase();
  const viaChoseong = hasChoseong(q);
  const penalty = viaChoseong ? 0.5 : 0;

  if (t.length === q.length && matchAt(t, q, 0)) return 0 + penalty;
  if (matchAt(t, q, 0)) return 1 + penalty;
  for (let s = 1; s + q.length <= t.length; s++) {
    if (matchAt(t, q, s)) return 2 + penalty;
  }
  return null;
}
