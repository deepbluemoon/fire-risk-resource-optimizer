<script setup lang="ts">
/** 운영 상태 — FR-024·FR-025·SC-004 배치 실패 노출, FR-043·SC-016 모델 문서화 */
import { computed, onMounted, ref } from 'vue';
import { api, MODEL_LABEL, dateTimeKo } from '../services/api';
import GradeBadge from '../components/GradeBadge.vue';

const batch = ref<any>(null);
const runs = ref<any[]>([]);
const models = ref<any[]>([]);
const grades = ref<any[]>([]);
const loading = ref(true);
const err = ref<string | null>(null);

/** 홀드아웃 지표를 모델별 한 줄로 압축한다 — SC-010 판정 근거를 화면에서 바로 본다 */
const KEY_METRIC: Record<string, { key: string; label: string; fmt: (v: number) => string; bar: string }> = {
  M1: { key: 'poisson_deviance', label: '예측 오차 (낮을수록 좋음)', fmt: (v) => v.toFixed(4), bar: '0.9080 미만' },
  M5: { key: 'brier', label: '확률 오차 (낮을수록 좋음)', fmt: (v) => v.toFixed(4), bar: '기본값보다 개선' },
  M2a: { key: 'pr_auc', label: '번짐 판별력 (높을수록 좋음)', fmt: (v) => v.toFixed(4), bar: '평균의 1.5배 이상' },
  M3: { key: 'top3', label: '상위 3개 안에 든 비율', fmt: (v) => `${(v * 100).toFixed(1)}%`, bar: '91.7% 초과' },
  M4: { key: 'official_recall_at_10', label: '큰 불을 미리 짚어낸 비율', fmt: (v) => `${(v * 100).toFixed(1)}%`, bar: '50% 초과' },
};

function keyMetric(m: any) {
  const spec = KEY_METRIC[m.modelId];
  const hm = m.holdoutMetrics ?? {};
  if (!spec || hm[spec.key] == null) return null;
  return { ...spec, value: spec.fmt(Number(hm[spec.key])) };
}

/** 일상어 정확도 — 통계 지표 대신 화면 맨 위에 둔다.
 *  'deviance 0.8869' 는 '88.9% 틀렸다' 로 오독된다. 백분율도 아니고 0 이 만점도
 *  아니기 때문이다. 사람이 검산할 수 있는 형태(예측 건수 vs 실제 건수)로 낸다. */
const acc = ref<any>(null);
const ACC_FROM = '2023-01-01';
const ACC_TO = '2023-12-31';
const pctOf = (v: number | null | undefined) =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;

/** failure_detail 은 '실패'만 담지 않는다. 배치는 성공 경로에서도 기상 공급원을
 *  {stage:'기상', source, weatherOk} 정보 항목으로 같이 남긴다 (run_daily.py 주석 참고).
 *  그걸 그대로 실패 목록에 찍으면 250/0 성공인 날에도 "실패 내용 · 전역 · 기상 ·" 이라는
 *  빈 줄이 떠서 멀쩡한 배치가 고장난 것처럼 보인다. reason 이 있는 항목만 실패로 본다. */
const fails = computed(() =>
  (batch.value?.failureDetail ?? []).filter((f: any) => f?.reason));
const wxNote = computed(() =>
  (batch.value?.failureDetail ?? []).find((f: any) => f?.stage === '기상' && !f?.reason) ?? null);

onMounted(async () => {
  try {
    const [b, r, m, g, a] = await Promise.all([
      api.batchStatus(), api.batchRuns(), api.models(), api.grades(),
      api.accuracy(ACC_FROM, ACC_TO).catch(() => null),
    ]);
    batch.value = b; runs.value = r; models.value = m; grades.value = g; acc.value = a;
  } catch (e: any) { err.value = e.message; }
  finally { loading.value = false; }
});
</script>

<template>
  <section>
    <header class="head">
      <div><p class="eyebrow">운영</p><h1>계산 상태와 모델 정보</h1></div>
    </header>

    <div v-if="err" class="tile err">{{ err }}</div>
    <div v-else-if="loading" class="tile muted">불러오는 중…</div>

    <template v-else>
      <div class="tile" :class="batch?.status !== '성공' && 'attn'">
        <h3>가장 최근 계산</h3>
        <dl class="dl">
          <dt>계산한 날짜</dt><dd class="mono">{{ batch?.targetDate ?? '—' }}</dd>
          <dt>상태</dt><dd :class="batch?.status !== '성공' && 'warn'">{{ batch?.status ?? '—' }}</dd>
          <dt>성공 / 실패 지역 수</dt><dd class="num">{{ batch?.regionsOk }} / {{ batch?.regionsFailed }}</dd>
          <dt>날씨 받아온 시각</dt>
          <dd :class="!batch?.forecastFetchedAt && 'warn'">
            {{ batch?.forecastFetchedAt ? dateTimeKo(batch.forecastFetchedAt) : '수집 안 됨' }}
          </dd>
          <dt v-if="wxNote">날씨 받아온 곳</dt>
          <dd v-if="wxNote" :class="!wxNote.weatherOk && 'warn'">
            {{ wxNote.source ?? '—' }}{{ wxNote.weatherOk ? '' : ' — 받지 못함' }}
          </dd>
          <dt>대체 계산 사용</dt><dd :class="batch?.fallbackSource && 'warn'">{{ batch?.fallbackSource ? '예 — 평균값으로 대체' : '아니오' }}</dd>
        </dl>
        <div v-if="fails.length" class="fails">
          <b>실패 내용 — 오류를 숨기지 않고 그대로 보여드립니다</b>
          <ul><li v-for="(f, i) in fails" :key="i">
            {{ f.regionCd ?? '전역' }} · {{ f.stage }} · {{ f.reason }}
          </li></ul>
        </div>
      </div>

      <!-- 예측이 실제로 얼마나 맞았나 — 사람이 검산할 수 있는 형태로 먼저 보여준다 -->
      <div v-if="acc" class="tile acc">
        <h3 class="ph">예측이 실제로 얼마나 맞았나 <span>{{ acc.range.from }} ~ {{ acc.range.to }}</span></h3>
        <div class="acc__row">
          <div class="acc__box">
            <span class="eyebrow">1년 전체 건수</span>
            <b class="num">{{ pctOf(acc.volume.accuracy) }}</b>
            <em>{{ Math.round(acc.volume.predicted).toLocaleString() }}건 예측 /
                 {{ Math.round(acc.volume.actual).toLocaleString() }}건 실제</em>
          </div>
          <div class="acc__box">
            <span class="eyebrow">확률이 맞은 정도</span>
            <b class="num">±{{ acc.probability.maxGapPp.toFixed(1) }}%p</b>
            <em>"가능성 50%"라고 하면 실제로 50% 안팎에서 발생</em>
          </div>
          <div class="acc__box">
            <span class="eyebrow">지역별 1년 합계</span>
            <b class="num">{{ pctOf(acc.region.within20) }}</b>
            <em>{{ acc.region.n }}곳 중 실제의 ±20% 안에 든 비율
                 (±10% 안: {{ pctOf(acc.region.within10) }})</em>
          </div>
        </div>
        <table class="data mini acc__tbl">
          <thead><tr><th>검산 예시 (화재 많은 5곳)</th><th class="r">예측</th><th class="r">실제</th><th class="r">차이</th></tr></thead>
          <tbody>
            <tr v-for="s in acc.region.samples" :key="s.sigungu">
              <td>{{ s.sigungu }}</td>
              <td class="num r">{{ Math.round(s.pred).toLocaleString() }}건</td>
              <td class="num r strong">{{ Math.round(s.act).toLocaleString() }}건</td>
              <td class="num r" :class="Math.abs(s.errPct) > 20 ? 'warn' : 'faint'">
                {{ s.errPct >= 0 ? '+' : '' }}{{ s.errPct.toFixed(1) }}%
              </td>
            </tr>
          </tbody>
        </table>
        <p class="foot">
          이 예측은 <b>"오늘 여기 불이 난다/안 난다"</b>를 맞히는 것이 아닙니다.
          하루 한 지역에 불이 날지는 우연이라 아무도 못 맞힙니다
          (실제로 {{ pctOf(acc.zeroRate) }}의 지역·일은 화재가 없었습니다).
          맞힐 수 있는 것은 <b>"며칠 중 몇 번", "가능성 몇 %", "어디가 더 위험한가"</b>이고,
          위 숫자가 그 결과입니다.
        </p>
      </div>

      <div class="grid">
        <div class="tile pad0">
          <h3 class="ph">예측 모델 목록 <span>어떤 모델이 무엇을 계산하는지</span></h3>
          <div class="scroll tblwrap">
          <table class="data mini">
            <thead><tr>
              <th>모델</th><th>버전</th><th>계산 방식</th><th>학습에 쓴 기간</th>
              <th class="r">사용 항목</th><th>검증 결과</th><th>합격 기준</th><th>기준 통과</th><th>사용 중</th>
            </tr></thead>
            <tbody>
              <tr v-for="m in models" :key="m.modelVersion">
                <td class="strong">{{ MODEL_LABEL[m.modelId] ?? m.modelId }}</td>
                <td class="mono">{{ m.modelVersion }}</td>
                <td class="faint algo">{{ m.algorithm }}</td>
                <td class="mono faint">{{ m.trainDataRange?.from }} ~ {{ m.trainDataRange?.to }}</td>
                <td class="num r">{{ m.features?.length ?? 0 }}</td>
                <td>
                  <template v-if="keyMetric(m)">
                    <span class="faint">{{ keyMetric(m)!.label }}</span>
                    <b class="num mv">{{ keyMetric(m)!.value }}</b>
                  </template>
                  <span v-else class="faint">—</span>
                </td>
                <td class="faint mono">{{ keyMetric(m)?.bar ?? '—' }}</td>
                <td :class="m.acceptancePassed ? 'ok' : 'warn'">{{ m.acceptancePassed ? '통과' : '미통과' }}</td>
                <td>{{ m.isActive ? '●' : '' }}</td>
              </tr>
            </tbody>
          </table>
          </div>
          <p class="note">
            모든 모델은 <b>학습에 쓰지 않은 2023년 실제 화재 기록</b>으로 검증합니다. 기준을 넘지 못하면 서비스에 넣지 않습니다.
            기준을 넘지 못한 모델은 원칙적으로 쓰지 않습니다. 다만 일부만 쓸모가 확인된 경우(예: 원인 예측)에는 <b>검증된 부분만</b> 골라 쓰고, 화면에 &lsquo;참고용&rsquo;으로 표시합니다.
            학습에 쓴 자료가 언제까지인지 숨기지 않고 그대로 보여드립니다. 모델이 낡는 것보다 낡은 줄 모르는 것이 더 위험하기 때문입니다.
          </p>
        </div>

        <div class="tile pad0">
          <h3 class="ph">위험 등급 기준 <span>확률이 어디부터 어느 등급인지</span></h3>
          <div class="scroll tblwrap">
            <table class="data mini grade-tbl">
              <thead><tr><th>등급</th><th class="r">이 확률부터</th><th class="r">이 확률까지</th><th>설명</th></tr></thead>
              <tbody>
                <tr v-for="g in grades" :key="g.grade">
                  <td><GradeBadge :grade="g.grade" /></td>
                  <td class="num r">{{ (Number(g.probLower) * 100).toFixed(1) }}%</td>
                  <td class="num r">{{ (Number(g.probUpper) * 100).toFixed(1) }}%</td>
                  <td class="faint wrap">{{ g.descriptionKo }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="tile pad0">
        <h3 class="ph">계산 기록 <span>최근 50회</span></h3>
        <div class="scroll tblwrap">
          <table class="data mini">
            <thead><tr><th class="r">회차</th><th>계산한 날짜</th><th>상태</th><th class="r">성공</th><th class="r">실패</th><th>대체 계산</th><th>끝난 시각</th></tr></thead>
            <tbody>
              <tr v-for="r in runs" :key="r.runId">
                <td class="num r faint">{{ r.runId }}</td>
                <td class="mono">{{ r.targetDate }}</td>
                <td :class="r.status !== '성공' && 'warn'">{{ r.status }}</td>
                <td class="num r">{{ r.regionsOk }}</td>
                <td class="num r" :class="r.regionsFailed > 0 && 'bad'">{{ r.regionsFailed }}</td>
                <td class="faint">{{ r.fallbackSource ?? '—' }}</td>
                <td class="faint mono">{{ dateTimeKo(r.finishedAt) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.head { margin-bottom: var(--sp-md); }
.attn { border-left: 3px solid var(--g-mid); }
.dl { display: grid; grid-template-columns: auto 1fr; gap: 5px var(--sp-md); margin: var(--sp-xs) 0 0; font-size: 13px; max-width: 620px; }
.dl dt { color: var(--ink-subtle); font-size: 12px; }
.dl dd { margin: 0; }
.fails { margin-top: var(--sp-sm); padding-top: var(--sp-xs); border-top: 1px solid var(--hairline); font-size: 12px; }
.fails b { color: var(--g-mid-ink); }
.fails ul { margin: 4px 0 0; padding-left: var(--sp-md); }

/* minmax(0,·) 이 없으면 넓은 표가 열을 밀어내 타일이 화면 밖으로 나간다 */
.grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
        gap: var(--sp-md); margin: var(--sp-md) 0; align-items: start; }
.grid > * { min-width: 0; }
@media (max-width: 1200px) { .grid { grid-template-columns: 1fr; } }
.pad0 { padding: 0; }
.ph { display: flex; align-items: baseline; gap: var(--sp-xs); padding: var(--sp-sm) var(--sp-md); border-bottom: 1px solid var(--hairline); }
.ph span { font-size: 10px; font-weight: 400; color: var(--ink-faint); font-family: var(--font-mono); }
.note { margin: 0; padding: var(--sp-sm) var(--sp-md); font-size: 11px; color: var(--ink-subtle); line-height: 1.5; border-top: 1px solid var(--hairline-soft); }
.tblwrap { max-height: 40vh; }
.mini th, .mini td { font-size: 12px; }
.strong { font-weight: 600; }
.algo { max-width: 260px; overflow: hidden; text-overflow: ellipsis; }
.mv { margin-left: 6px; }
.faint { color: var(--ink-faint); }
.warn { color: var(--g-mid-ink); }
.bad { color: var(--g-critical-ink); }
.ok { color: var(--g-low-ink); }
.err { border-left: 3px solid var(--g-critical); color: var(--g-critical-ink); }
.muted { color: var(--ink-subtle); }

/* 긴 설명은 줄바꿈한다 — table.data td 의 기본값이 nowrap 이라
   한 줄로 늘어나며 표가 화면 밖으로 밀려났다. */
.wrap { white-space: normal; line-height: 1.5; }
/* 등급 표는 열 폭을 고정해 설명이 아무리 길어도 타일을 밀지 않게 한다 */
.grade-tbl { table-layout: fixed; width: 100%; min-width: 0; }
.grade-tbl th:nth-child(1), .grade-tbl td:nth-child(1) { width: 22%; }
.grade-tbl th:nth-child(2), .grade-tbl td:nth-child(2),
.grade-tbl th:nth-child(3), .grade-tbl td:nth-child(3) { width: 19%; }
.grade-tbl th:nth-child(4), .grade-tbl td:nth-child(4) { width: 40%; }
/* 표를 담은 스크롤 상자가 타일 폭을 넘지 않게 한다 */
.tblwrap { max-width: 100%; }

/* 일상어 정확도 — 통계 지표 대신 맨 위에 온다 */
.acc { padding: 0; margin-bottom: var(--sp-md); }
.acc__row { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 1px; background: var(--hairline); }
.acc__box { padding: var(--sp-sm) var(--sp-md); background: var(--surface); }
.acc__box b { display: block; margin: 3px 0 2px; font-size: 26px; font-weight: 300; }
.acc__box em { font-size: 11px; font-style: normal; color: var(--ink-muted); line-height: 1.5; }
.acc__tbl { margin-top: 1px; }
.acc__tbl .strong { font-weight: 600; }
.acc .foot { margin: 0; padding: var(--sp-sm) var(--sp-md);
             border-top: 1px solid var(--hairline); font-size: 11px; line-height: 1.65;
             color: var(--ink-muted); }
.acc .foot b { color: var(--ink); }
</style>
