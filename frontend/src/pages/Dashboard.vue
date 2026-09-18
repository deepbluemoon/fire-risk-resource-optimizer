<script setup lang="ts">
/** US1 — 당일 시군구별 화재 발생 예측 확인 (P1, MVP)
 *  FR-014 정렬 목록 · FR-021 등급/색상/문장 · SC-006 251행 전부 표시 */
import { computed, onMounted, ref, watch } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import { api, pct, num, today, GRADE_ORDER, type RiskDaily, type RiskItem } from '../services/api';
import { useAppStore } from '../stores/app';
import GradeBadge from '../components/GradeBadge.vue';
import FreshnessBanner from '../components/FreshnessBanner.vue';
import WeatherNow from '../components/WeatherNow.vue';
import RiskSpectrum from '../components/RiskSpectrum.vue';
import { scoreMatch, hasChoseong } from '../util/hangul';

const app = useAppStore();
const router = useRouter();

const data = ref<RiskDaily | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const date = ref(app.date);
const sido = ref('');
/** 입력칸에 보이는 값(qInput)과 실제로 거르는 값(q)을 나눈다.
 *
 *  한글은 'ㅎ → 하 → 화' 처럼 한 글자가 여러 단계를 거쳐 완성된다. 입력을 그대로
 *  걸러내기에 쓰면 그 중간 단계마다 목록이 튀어 읽을 수가 없다. 조합이 끝난 뒤에,
 *  그것도 잠깐 쉰 뒤에 한 번만 거른다. */
const qInput = ref('');
const q = ref('');
let composing = false;
let settleTimer: number | undefined;

function settle() {
  window.clearTimeout(settleTimer);
  settleTimer = window.setTimeout(() => { q.value = qInput.value; }, 180);
}
function onSearchInput() { if (!composing) settle(); }
function onCompositionStart() { composing = true; }
function onCompositionEnd() { composing = false; settle(); }
function clearSearch() {
  window.clearTimeout(settleTimer);
  qInput.value = ''; q.value = '';
}
const minGrade = ref('');
type SortKey = 'count' | 'probability' | 'damage' | 'spread' | 'longburn';
const sortBy = ref<SortKey>('probability');
const selected = ref<string | null>(null);

/** '예상 화재 건수' 는 정렬 기준에서 뺐다.
 *
 *  발생 확률은 기대 건수 λ 를 1-exp(-λ) 로 옮기고 보정한 값이라 증가함수다.
 *  그래서 두 기준의 줄 순서가 251곳 전부 똑같았다(역전 0건). 선택지는 둘인데
 *  결과가 하나면 화면이 고르라고 해놓고 아무것도 안 고르게 하는 셈이다.
 *  더 읽기 쉬운 쪽(확률)만 남긴다. 건수는 표의 열로는 그대로 보인다. */
const SORTS = [
  { key: 'probability', label: '화재가 날 가능성', note: '한 건이라도 날 것인가' },
  { key: 'damage', label: '대응 부담', note: '얼마나 손이 많이 가는가' },
  { key: 'spread', label: '불이 번질 가능성', note: '얼마나 크게 번지는가' },
  { key: 'longburn', label: '진화가 길어질 가능성', note: '얼마나 오래 걸리는가' },
] as const;

/** 계산되지 않은 기준은 누른다 — 같은 순위를 다른 이름으로 보이면 화면이 거짓말을 한다 */
const usable = (k: string) => (data.value?.available as any)?.[k] ?? true;
const blocked = computed(() => SORTS.filter((s) => !usable(s.key)).map((s) => s.label));
/** 그래프 머리말이 현재 정렬 기준을 그대로 말하게 한다 */
const sortLabel = computed(() =>
  SORTS.find((s) => s.key === sortBy.value)?.label ?? '위험');

async function load() {
  loading.value = true; error.value = null;
  try {
    const d = await api.riskDaily({
      date: date.value,
      sortBy: sortBy.value,
    });
    data.value = d;
    app.setFreshness(d.freshness);
    app.date = d.targetDate;
  } catch (e: any) { error.value = e.message; }
  finally { loading.value = false; }
}

onMounted(async () => {
  await app.bootstrap();
  // 앱 스토어의 date 는 '가장 최근 배치가 계산한 날' 이라 앞으로의 날짜일 수 있다.
  // 이 화면은 오늘만 보여주므로 서울 기준 오늘을 그대로 쓴다.
  date.value = today();
  load();
});
// 정렬은 여기서 하지 않는다. 251행이 이미 와 있는데 서버를 다시 부르면
// 그동안 본문이 '불러오는 중…' 한 줄로 바뀌어 페이지 높이가 무너지고,
// 스크롤이 맨 위로 튄다. 시도·등급만 서버가 다시 골라 준다.
// 시도·등급 모두 서버에 다시 묻지 않는다. 251행이 이미 와 있고, 걸러서 받으면
// 시도 목록 자체가 그 시도 하나로 줄어들어 다른 시도로 바로 갈 수 없었다.

/** 목록은 API 가 선택한 기준으로 정렬해서 준다. 그러니 순위도 그 순서로 다시 매긴다.
 *
 *  예전에는 DB 의 risk_rank 를 그대로 찍었는데, 그 값은 배치가 '예상 화재 건수'
 *  기준으로 매긴 고정 순위다. 그래서 '불이 번질 가능성' 으로 정렬하면 순위가
 *  3, 1, 7, 2 … 처럼 뒤죽박죽 보였다. 보이는 순서와 순위가 어긋나면 안 된다.
 *  데이터가 없는 지역은 순위를 매기지 않는다. */
/** 정렬 기준마다 어떤 값을 보는지. 서버가 쓰던 것과 같은 값이다. */
const SORT_FIELD: Record<SortKey, string> = {
  count: 'expectedCount',
  probability: 'occurProbability',
  damage: 'expectedDamageLoad',
  spread: 'spreadProbability',
  longburn: 'longburnProbability',
};

const items = computed(() => {
  const raw = [...(data.value?.items ?? [])] as any[];
  const field = SORT_FIELD[sortBy.value];
  // 값이 없는 지역은 크기를 견줄 수 없으니 언제나 맨 뒤로 보낸다.
  raw.sort((a, b) => {
    const x = a[field], y = b[field];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (Number(y) !== Number(x)) return Number(y) - Number(x);
    // 동점이 흔하다 — '화재가 날 가능성' 은 세 곳이 63.8% 로 같기도 하다.
    // 순서를 운에 맡기면 같은 화면을 두 번 봤을 때 줄이 뒤바뀌어 보인다.
    // 예상 건수가 많은 쪽을 위에 두고, 그것마저 같으면 이름으로 못박는다.
    const cx = Number(a.expectedCount ?? 0), cy = Number(b.expectedCount ?? 0);
    if (cy !== cx) return cy - cx;
    return String(a.regionCd).localeCompare(String(b.regionCd));
  });
  let n = 0;
  return raw.map((it: any) => ({
    ...it,
    displayRank: it.dataStatus === '데이터없음' ? null : ++n,
  }));
});
/** 지역 검색 — 화면에서만 거른다.
 *
 *  251행이 이미 와 있으므로 글자마다 서버를 다시 부를 이유가 없다. 그리고 순위는
 *  거르기 전 전국 기준을 그대로 쓴다. '화성시가 전국 1위' 라는 사실이 검색했다고
 *  1위가 되어버리면 안 된다. 시도 이름으로도 찾을 수 있게 둘 다 본다. */
const visible = computed(() => {
  const s = q.value.trim();
  if (!s) return inSido.value;
  // 시군구로 맞은 것을 시도로만 맞은 것보다 항상 위에 둔다 (+10).
  // 같은 점수끼리는 정렬이 안정적이라 위험도 순서가 그대로 유지된다.
  const hit: Array<{ it: any; score: number }> = [];
  for (const it of inSido.value) {
    const bySigungu = scoreMatch(it.sigungu ?? '', s);
    if (bySigungu !== null) { hit.push({ it, score: bySigungu }); continue; }
    const bySido = scoreMatch(it.sido ?? '', s);
    if (bySido !== null) hit.push({ it, score: bySido + 10 });
  }
  return hit.sort((a, b) => a.score - b.score).map((h) => h.it);
});

/** 시도·등급으로 고른 범위. 둘 다 안 골랐으면 전부.
 *
 *  등급은 '이 등급 이상' 이 아니라 **그 등급만** 이다. '보통' 을 고르면 보통만 남는다.
 *  '이상' 이었을 때는 맨 아래 등급을 고르는 것이 전체와 같아져 선택지가 헛돌았다. */
const inSido = computed(() => {
  let r = items.value;
  if (sido.value) r = r.filter((i) => i.sido === sido.value);
  if (minGrade.value) r = r.filter((i) => i.riskGrade === minGrade.value);
  return r;
});

/** 지금 고른 범위를 사람 말로. 둘 다 골랐으면 '경기도 · 보통'. */
const scopeLabel = computed(() =>
  [sido.value, minGrade.value].filter(Boolean).join(' · '));

/** 색을 살릴 지역들. 아무것도 안 골랐으면 null — 전부 색으로 둔다. */
const activeKeys = computed(() =>
  (sido.value || minGrade.value)
    ? new Set(inSido.value.map((i) => i.regionCd))
    : null);

/** 목록은 언제나 전체 251곳에서 만든다 — 그래야 다른 시도로 바로 건너갈 수 있다. */
const sidos = computed(() => [...new Set(items.value.map((i) => i.sido))].sort());
const counts = computed(() => {
  const c: Record<string, number> = { '매우높음': 0, '높음': 0, '보통': 0, '낮음': 0 };
  for (const i of inSido.value) if (i.riskGrade) c[i.riskGrade]++;
  return c;
});
const noData = computed(() => inSido.value.filter((i) => i.dataStatus === '데이터없음').length);
const proxy = computed(() => inSido.value.filter((i) => i.confidence === '대리지점').length);
const totalExpected = computed(() => inSido.value.reduce((s, i) => s + Number(i.expectedCount ?? 0), 0));

function open(it: RiskItem) {
  router.push(`/region/${encodeURIComponent(it.regionCd)}/${data.value!.targetDate}`);
}
function metric(it: RiskItem) {
  return sortBy.value === 'probability' ? pct(it.occurProbability)
       : sortBy.value === 'damage' ? num(it.expectedDamageLoad)
       : num(it.expectedCount);
}
</script>

<template>
  <section>
    <header class="head">
      <div>
        <p class="eyebrow">
          오늘의 위험도
          <template v-if="data?.targetDate"> · <span class="mono">{{ data.targetDate }}</span></template>
        </p>
        <h1>관할 시군구 화재 발생 예측</h1>
      </div>
      <div class="head__ctl">
        <label class="lbl">지역 검색
          <input v-model="qInput" type="search" class="field field--find"
                 placeholder="시군구 · 시도"
                 aria-label="지역 검색 — 초성으로도 찾을 수 있습니다"
                 @input="onSearchInput"
                 @compositionstart="onCompositionStart"
                 @compositionend="onCompositionEnd"
                 @keydown.esc="clearSearch" />
        </label>
        <label class="lbl">시도
          <select v-model="sido" class="field">
            <option value="">전체</option>
            <option v-for="s in sidos" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="lbl">등급
          <select v-model="minGrade" class="field">
            <option value="">전체</option>
            <option v-for="g in GRADE_ORDER" :key="g" :value="g">{{ g }}</option>
          </select>
        </label>
      </div>
    </header>

    <FreshnessBanner :freshness="data?.freshness ?? null" />

    <!-- 내 위치의 지금 날씨. 등급만 보면 '왜 오늘 높은지' 가 안 보인다. -->
    <WeatherNow v-if="data" :date="data.targetDate" />

    <div v-if="error" class="tile err">{{ error }}</div>
    <div v-else-if="loading" class="tile muted">불러오는 중…</div>

    <!-- 배치가 실패해 오늘 예측이 없을 때. 빈 표를 보여주면 고장으로 읽힌다. -->
    <div v-else-if="data && !items.length" class="tile muted nodate">
      <b>오늘 예측이 아직 준비되지 않았습니다.</b>
      <p>
        위험도는 매일 새벽에 한 번 계산됩니다. 계산이 끝나면 이 화면에 표시됩니다.
        계속 비어 있다면 <RouterLink to="/ops">계산·모델 상태</RouterLink> 에서 계산이
        실패하지 않았는지 확인해 주세요.
      </p>
    </div>

    <template v-else-if="data">
      <div class="stats">
        <!-- 다섯 칸을 같은 구조로 둔다 — 위에 이름, 아래에 숫자.
             예전에는 앞 네 칸만 숫자를 오른쪽 끝으로 밀고 마지막 칸만 세로였다.
             그래서 숫자가 한 줄에 맞지 않아 훑어보기 어려웠다. -->
        <div v-for="g in GRADE_ORDER" :key="g" class="stat" :class="{ 'stat--on': minGrade === g }">
          <div class="stat__k"><GradeBadge :grade="g" /></div>
          <p class="stat__v"><b class="num">{{ counts[g] }}</b><span>곳</span></p>
        </div>
        <div class="stat">
          <div class="stat__k"><span class="eyebrow">오늘 전체 예상 화재</span></div>
          <p class="stat__v"><b class="num">{{ totalExpected.toFixed(1) }}</b><span>건</span></p>
        </div>
      </div>

      <div class="tile spectrum">
        <p class="eyebrow">
          <template v-if="scopeLabel">
            {{ scopeLabel }} {{ inSido.length }}개 시군구 · {{ sortLabel }} 순
            <span class="muted">— 전국 {{ items.length }}곳 중 어디쯤인지 함께 보여드립니다</span>
          </template>
          <template v-else>관할 {{ items.length }}개 시군구 · {{ sortLabel }} 순</template>
        </p>
        <RiskSpectrum :items="items" :selected="selected" :metric="sortBy" :active="activeKeys"
                      @pick="(i) => { selected = i.regionCd; open(i); }" />
      </div>

      <div class="sortbar">
        <span class="eyebrow">정렬 기준</span>
        <button v-for="s in SORTS" :key="s.key" class="btn btn--sm"
                :aria-pressed="sortBy === s.key" :disabled="!usable(s.key)"
                :title="usable(s.key) ? '' : `'${s.label}' 은 오늘 계산되지 않았습니다`"
                @click="usable(s.key) && (sortBy = s.key as any)">
          {{ s.label }}<em>{{ usable(s.key) ? s.note : '오늘은 없음' }}</em>
        </button>
        <span class="sortbar__note">
          <template v-if="q.trim()">
            '{{ q.trim() }}' 로 {{ visible.length }}곳<template v-if="hasChoseong(q)"> (초성)</template>
            — 잘 맞는 순으로 놓았고, 순위는 전국 기준 그대로입니다.
          </template>
          <template v-else-if="blocked.length">
            {{ blocked.join(' · ') }} 은(는) 오늘 계산되지 않아 선택할 수 없습니다.
          </template>
          <template v-else>
            기준을 바꾸면 순위가 달라집니다 — 불이 자주 나는 곳과 크게 번지는 곳은 다릅니다.
          </template>
        </span>
      </div>

      <div class="tile pad0">
        <div class="scroll tblwrap">
          <table class="data">
            <thead>
              <tr>
                <th class="r">순위</th><th>시도</th><th>시군구</th><th>위험 등급</th>
                <th class="r">화재가 날 가능성</th><th class="r">예상 화재 건수</th>
                <th class="r">불이 번질 가능성</th><th class="r" title="예상 화재 건수를 '손이 가는 정도'로 환산한 값입니다. 예상 건수 × (1+번질 가능성) × (1+길어질 가능성). 대수·인원이 아니라 지역끼리 견주는 상대값입니다.">대응 부담</th>
                <th>예측 신뢰도</th><th>날씨 반영</th><th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="it in visible" :key="it.regionCd"
                  :class="{ 'row--nodata': it.dataStatus === '데이터없음' }"
                  @click="open(it)">
                <td class="num r">{{ it.displayRank ?? '—' }}</td>
                <td>{{ it.sido }}</td>
                <td class="strong">{{ it.sigungu }}</td>
                <td><GradeBadge :grade="it.riskGrade" :status="it.dataStatus" /></td>
                <td class="num r">{{ pct(it.occurProbability) }}</td>
                <td class="num r">{{ num(it.expectedCount) }}</td>
                <td class="num r" :class="it.spreadProbability == null ? 'faint'
                                          : (it.spreadProbability > 0.297 ? 'hot' : '')">
                  {{ it.spreadProbability != null ? pct(it.spreadProbability, 0) : '—' }}
                </td>
                <td class="num r" :class="it.expectedDamageLoad == null && 'faint'">
                  {{ it.expectedDamageLoad != null ? num(it.expectedDamageLoad) : '—' }}
                </td>
                <td :class="it.confidence !== '정상' ? 'warn' : 'faint'">
                  {{ it.confidence === '정상' ? '보통' : it.confidence === '대리지점' ? '조금 낮음' : '낮음' }}
                </td>
                <td :class="it.dataStatus !== '정상' ? 'warn' : 'faint'">
                  {{ it.dataStatus === '정상' ? '반영됨' : it.dataStatus === '데이터없음' ? '자료 없음' : '반영 안 됨' }}
                </td>
                <td class="faint">›</td>
              </tr>
              <tr v-if="!visible.length && q.trim()" class="row--empty">
                <td colspan="11">
                  '{{ q.trim() }}' 와 맞는 지역이 없습니다.
                  이름의 일부나 초성으로 찾을 수 있습니다 — 예를 들어 '화성' 도 'ㅎㅅ' 도 됩니다.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <p class="foot">
        {{ scopeLabel || '전체' }} <b class="num">{{ inSido.length }}</b>개 시군구
        <template v-if="noData"> · 자료 없음 <b class="num warn">{{ noData }}</b>개 — 값을 지어내지 않고 비워 둡니다</template>
        <template v-if="proxy"> · 이웃 지역 날씨를 쓴 곳 <b class="num warn">{{ proxy }}</b>개</template>
      </p>
    </template>
  </section>
</template>

<style scoped>
.head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--sp-lg); margin-bottom: var(--sp-md); flex-wrap: wrap; }

.stats { display: flex; gap: 1px; margin: var(--sp-md) 0; flex-wrap: wrap; }
.stat {
  display: flex; flex-direction: column; gap: var(--sp-xs);
  padding: var(--sp-sm) var(--sp-md); background: var(--surface-1);
  border: 1px solid var(--hairline); flex: 1 1 150px; min-width: 150px;
}
/* 이름 줄의 높이를 고정한다. 등급 배지와 글자 라벨은 높이가 달라서,
   그대로 두면 아래 숫자들이 칸마다 조금씩 어긋난다. */
.stat__k { display: flex; align-items: center; min-height: 22px; }
.stat__v { display: flex; align-items: baseline; gap: 4px; margin: 0; }
.stat__v b { font-size: 26px; font-weight: 300; line-height: 1.05; }
.stat__v span { font-size: 11px; color: var(--ink-subtle); }

.spectrum { margin-bottom: var(--sp-md); }
.spectrum .eyebrow { margin: 0 0 var(--sp-xs); }

.sortbar { display: flex; align-items: center; gap: var(--sp-xs); margin-bottom: var(--sp-xs); flex-wrap: wrap; }
.sortbar .btn { display: inline-flex; align-items: baseline; gap: 6px; }
.sortbar em { font-style: normal; font-size: 10px; color: var(--ink-faint); font-family: var(--font-mono); }
.sortbar .btn[aria-pressed='true'] em { color: rgba(255,255,255,.75); }
.sortbar__note { margin-left: auto; font-size: 11px; color: var(--ink-subtle); }

.pad0 { padding: 0; }
.tblwrap { max-height: calc(100vh - 470px); min-height: 260px; }
table.data tbody tr { cursor: pointer; }
.row--nodata { color: var(--ink-faint); background: repeating-linear-gradient(135deg, transparent 0 6px, rgba(255,255,255,.02) 6px 12px); }
.strong { font-weight: 500; }
.faint { color: var(--ink-faint); font-size: 12px; }
.warn { color: var(--g-mid-ink); font-size: 12px; }
.hot { color: var(--g-high-ink); }
.err { border-left: 3px solid var(--g-critical); color: var(--g-critical-ink); }
.muted { color: var(--ink-subtle); }
/* 대상일은 고를 수 없으므로 입력칸이 아니라 값으로 보여준다.
   입력칸처럼 보이면 눌러도 안 바뀌어 고장으로 읽힌다. */
/* 검색칸은 시도·등급 선택칸보다 넓어야 지역 이름이 잘리지 않는다 */
.field--find { width: 150px; }
/* 결과가 없을 때도 표의 리듬을 유지한다 — 글자만 가운데로 띄우면 칸이 무너져 보인다 */
.row--empty td { color: var(--ink-subtle); text-align: center; padding: var(--sp-lg); }
.row--empty:hover { background: transparent; }

/* 예측이 없을 때 안내 — 빈 표 대신 왜 비었는지를 말한다 */
.nodate { line-height: 1.7; }
.nodate > b { display: block; margin-bottom: 4px; color: var(--ink); font-size: 14px; }
.nodate p { margin: 0; font-size: 12px; }
.nodate p b { color: var(--ink-muted); font-family: var(--font-mono); }
.foot { margin-top: var(--sp-sm); font-size: 12px; color: var(--ink-subtle); }
.foot b { color: var(--ink); }
</style>
