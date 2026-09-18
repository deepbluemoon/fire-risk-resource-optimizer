<script setup lang="ts">
/** US5 — 예측 이력 조회 및 사후 검증
 *  FR-018 대조 · FR-019 오경보/미탐지 · SC-007 유의성 · SC-011 캘리브레이션 */
import { computed, onMounted, ref } from 'vue';
import { api, pct } from '../services/api';
import GradeBadge from '../components/GradeBadge.vue';

const from = ref('2023-01-01');
const to = ref('2023-12-31');
const sido = ref('');
const summary = ref<any>(null);
const rows = ref<any[]>([]);
const loading = ref(true);
const err = ref<string | null>(null);

async function load() {
  loading.value = true; err.value = null;
  try {
    const [s, h] = await Promise.all([
      api.verifySummary(from.value, to.value, sido.value || undefined),
      api.verifyHistory(from.value, to.value),
    ]);
    summary.value = s; rows.value = h;
  } catch (e: any) { err.value = e.message; }
  finally { loading.value = false; }
}
onMounted(load);

const months = computed(() => Object.entries(summary.value?.calibration ?? {}) as [string, number][]);
const worst = computed(() => months.value.reduce((m, [, v]) => Math.max(m, Math.abs(v - 1)), 0));
const sidos = computed(() => [...new Set(rows.value.map((r) => r.sido))].sort());
/** 결과 이름 — '오경보' 같은 말은 모델이 틀린 것처럼 읽힌다. 확률 예측을 O/X 로
 *  압축하면 확률이 정확해도 절반은 '틀린' 것으로 분류된다. 중립적으로 쓴다. */
const OUTCOME_KO: Record<string, string> = {
  hit: '위험 표시 · 실제 발생',
  false_alarm: '위험 표시 · 발생 없음',
  miss: '위험 표시 없음 · 실제 발생',
  correct_reject: '위험 표시 없음 · 발생 없음',
};

const grades = computed<any[]>(() => summary.value?.grades ?? []);
/** 등급 사이 발생률이 실제로 벌어지는지 — 이 화면의 핵심 판정 */
const separates = computed(() => {
  const g = grades.value;
  if (g.length < 2) return false;
  return g.every((cur, i) => i === 0 || cur.actualRate >= g[i - 1].actualRate);
});
</script>

<template>
  <section>
    <header class="head">
      <div>
        <p class="eyebrow">지난 예측 확인</p>
        <h1>예측이 실제와 맞았는지 확인</h1>
      </div>
      <div class="head__ctl">
        <label class="lbl">시작<input v-model="from" type="date" class="field" @change="load" /></label>
        <label class="lbl">종료<input v-model="to" type="date" class="field" @change="load" /></label>
        <label class="lbl">시도
          <select v-model="sido" class="field" @change="load">
            <option value="">전체</option><option v-for="s in sidos" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
      </div>
    </header>

    <div v-if="err" class="tile err">{{ err }}</div>
    <div v-else-if="loading" class="tile muted">불러오는 중…</div>

    <template v-else-if="summary">
      <!-- 주 지표: 등급별 예측 확률 vs 실제 발생률.
           4칸 O/X 요약을 먼저 보여주면 '헛경보가 절반' 으로 오독된다. -->
      <div class="tile">
        <h3>등급별로 실제 결과가 어땠나요</h3>
        <p class="hint">
          위험 등급이 올라갈수록 실제로 불이 더 많이 났는지 봅니다.
          <b>예상</b>과 <b>실제</b>가 가까울수록 예측이 정확한 것입니다.
        </p>
        <table class="data grades">
          <thead>
            <tr>
              <th>위험 등급</th><th class="r">해당 지역·일</th>
              <th class="r">예상 발생률</th><th class="r">실제 발생률</th>
              <th class="r">차이</th><th class="r">실제 화재</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="g in grades" :key="g.grade">
              <td><GradeBadge :grade="g.grade" /></td>
              <td class="num r">{{ g.n.toLocaleString() }}</td>
              <td class="num r">{{ pct(g.predRate, 1) }}</td>
              <td class="num r strong">{{ pct(g.actualRate, 1) }}</td>
              <td class="num r" :class="Math.abs(g.gapPp) > 5 ? 'warn' : 'faint'">
                {{ g.gapPp >= 0 ? '+' : '' }}{{ g.gapPp.toFixed(1) }}%p
              </td>
              <td class="num r faint">{{ g.fires.toLocaleString() }}건</td>
            </tr>
          </tbody>
        </table>
        <p class="verdict" :class="separates && summary.maxGapPp <= 5 ? 'pass' : 'fail'">
          <template v-if="separates && summary.maxGapPp <= 5">
            등급이 올라갈수록 실제 발생률도 함께 올라갔고, 예상과 실제의 차이가
            최대 {{ summary.maxGapPp.toFixed(1) }}%p 로 작습니다 — 예측이 제 역할을 했습니다.
          </template>
          <template v-else-if="separates">
            등급 순서는 맞지만 예상과 실제가 최대 {{ summary.maxGapPp.toFixed(1) }}%p 벌어졌습니다.
          </template>
          <template v-else>
            등급이 올라가도 실제 발생률이 따라 오르지 않았습니다.
          </template>
        </p>
      </div>

      <!-- 보조: O/X 로 압축했을 때. 왜 '발생 없음' 이 많아 보이는지 함께 설명한다. -->
      <div class="tile">
        <h3>'높음 이상'만 골라 보면</h3>
        <p class="hint">
          위 표를 <b>높음·매우높음 = 주의</b>, <b>낮음·보통 = 비주의</b> 둘로만 나눈 것입니다.
        </p>
        <div class="matrix">
          <div class="mx mx--hit"><span class="eyebrow">주의 → 실제 발생</span>
            <b class="num">{{ summary.hit.toLocaleString() }}</b><em>미리 대비할 수 있었던 경우</em></div>
          <div class="mx mx--fa"><span class="eyebrow">주의 → 발생 없음</span>
            <b class="num">{{ summary.falseAlarm.toLocaleString() }}</b><em>대비했으나 불이 안 난 경우</em></div>
          <div class="mx mx--miss"><span class="eyebrow">비주의 → 실제 발생</span>
            <b class="num">{{ summary.miss.toLocaleString() }}</b><em>대비 없이 불이 난 경우</em></div>
          <div class="mx"><span class="eyebrow">비주의 → 발생 없음</span>
            <b class="num">{{ summary.correctReject.toLocaleString() }}</b><em>조용히 지나간 경우</em></div>
        </div>
        <dl class="dl">
          <dt>주의 표시한 곳에서 실제로 불이 난 비율</dt>
          <dd class="num">{{ pct(summary.precision, 1) }}
            <span class="vs">표시 안 했으면 {{ pct(summary.overallRate, 1) }}</span></dd>
          <dt>실제 불이 난 곳 중 미리 짚어낸 비율</dt>
          <dd class="num">{{ pct(summary.recall, 1) }}</dd>
        </dl>
        <p class="caveat caveat-ref">
          <b>'발생 없음'이 많은 것은 예측이 틀렸다는 뜻이 아닙니다</b>
          이 예측은 "불이 난다/안 난다"가 아니라 <b>"불이 날 가능성 {{ pct(summary.highGradeRate, 0) }}"</b>
          라고 말한 것입니다. 가능성이 {{ pct(summary.highGradeRate, 0) }}면 나머지는 안 나는 것이 정상이고,
          위 표에서 예상과 실제가 거의 같다는 것이 그 근거입니다.
          <br />
          오히려 눈여겨보실 것은 <b class="warn">대비 없이 불이 난 {{ summary.miss.toLocaleString() }}건</b>입니다.
          주의 범위를 넓히면 이 숫자가 줄지만 '대비했으나 안 난 경우'가 함께 늘어납니다 — 둘은 맞바꾸는 관계입니다.
        </p>
      </div>

      <div class="grid">
        <div class="tile">
          <h3>우연이라고 보기 어려운 차이인가요</h3>
          <p class="hint">
            표본이 적으면 우연히 차이가 나 보일 수 있습니다. 그 가능성을 걸러낸 결과입니다.
          </p>
          <dl class="dl">
            <dt>주의 표시 지역의 실제 발생률</dt><dd class="num">{{ pct(summary.highGradeRate, 1) }}</dd>
            <dt>전체 평균 발생률</dt><dd class="num">{{ pct(summary.overallRate, 1) }}</dd>
            <dt>평균 대비</dt><dd class="num">{{ Number(summary.lift).toFixed(2) }}배</dd>
            <dt>우연일 가능성</dt>
            <dd class="num">{{ summary.significant ? '거의 없음' : '배제 못 함' }}</dd>
          </dl>
          <p class="verdict" :class="summary.significant ? 'pass' : 'fail'">
            {{ summary.significant
               ? '우연으로 보기 어려운 차이입니다 — 등급이 실제 위험을 가려내고 있습니다'
               : '표본이 적어 우연일 가능성을 배제할 수 없습니다' }}
          </p>
        </div>

        <div class="tile">
          <h3>확률이 실제와 얼마나 일치했나요</h3>
          <p class="hint">달마다 "예상한 건수"와 "실제 건수"가 얼마나 가까웠는지 봅니다. 막대가 가운데 선에 가까울수록 잘 맞은 것입니다.</p>
          <div class="cal">
            <div v-for="[ym, v] in months" :key="ym" class="cal__col"
                 :class="{ bad: Math.abs(v - 1) > 0.15 }"
                 :title="`${ym}: ${(v * 100).toFixed(1)}%`">
              <div class="cal__bar" :style="{ height: `${Math.min(100, v * 50)}%` }" />
              <span class="cal__x mono">{{ ym.slice(5) }}</span>
            </div>
            <div class="cal__base" />
          </div>
          <p class="verdict" :class="worst <= 0.15 ? 'pass' : 'fail'">
            가장 많이 어긋난 달이 {{ (worst * 100).toFixed(0) }}% 차이 — {{ worst <= 0.15 ? '허용 범위(15%) 안입니다' : '허용 범위(15%)를 넘었습니다' }}
          </p>
        </div>

        <div class="tile">
          <h3>불이 번질 가능성 예측</h3>
          <p class="hint">"번지기 쉽다"고 본 상위 20%에서 실제로 불이 번진 비율입니다. 피해 금액이 아니라 번짐 자체로 봅니다.</p>
          <template v-if="summary.spread">
            <dl class="dl">
              <dt>번질 것으로 본 곳의 실제 번짐 비율</dt><dd class="num">{{ pct(summary.spread.topQuintileEscalationRate, 1) }}</dd>
              <dt>전체 평균</dt><dd class="num">{{ pct(summary.spread.overallEscalationRate, 1) }}</dd>
              <dt>확인한 사례</dt><dd class="num faint">{{ Number(summary.spread.nRegionDays).toLocaleString() }}건</dd>
            </dl>
            <p class="verdict" :class="Number(summary.spread.lift) > 1.5 ? 'pass' : 'fail'">
              전체 평균보다 {{ Number(summary.spread.lift).toFixed(1) }}배 잘 골라냈습니다
            </p>
          </template>
          <p v-else class="muted small">이 기간에는 번짐 예측이 계산되지 않았습니다.</p>
        </div>

        <div class="tile">
          <h3>화재 원인 예측</h3>
          <p class="hint">실제 화재 원인이 예측한 후보 3개 안에 들어 있었는지 봅니다.</p>
          <template v-if="summary.cause">
            <dl class="dl">
              <dt>후보 3개 안에 든 비율</dt><dd class="num">{{ pct(summary.cause.top3Accuracy, 2) }}</dd>
              <dt>가장 흔한 3개를 그냥 찍었을 때</dt><dd class="num">{{ pct(summary.cause.constantBaseline, 1) }}</dd>
              <dt>확인한 사례</dt>
              <dd class="num faint">
                {{ Number(summary.cause.matched).toLocaleString() }} / {{ Number(summary.cause.total).toLocaleString() }}
              </dd>
            </dl>
            <p class="verdict fail">
              가장 흔한 3개를 그냥 찍는 것과 결과가 같습니다. 그래서 순위는 쓰지 않고,
              지역 상세 화면에서 '평소와 다른 원인'만 보여드립니다.
            </p>
          </template>
          <p v-else class="muted small">이 기간에는 원인 예측이 계산되지 않았습니다.</p>
        </div>

        <div class="tile">
          <h3>상위 20% 포착률</h3>
          <p class="hint">기대 건수 상위 20% 셀이 포착한 실제 발생 비율. 무작위 배치는 20%입니다.</p>
          <p class="big num">{{ pct(summary.recallAt20) }}</p>
          <p class="verdict" :class="summary.recallAt20 > 0.2 ? 'pass' : 'fail'">
            무작위 대비 {{ (summary.recallAt20 / 0.2).toFixed(2) }}배
          </p>
        </div>
      </div>

      <div class="tile pad0">
        <div class="scroll tblwrap">
          <table class="data">
            <thead><tr>
              <th>일자</th><th>시도</th><th>시군구</th><th>예측 등급</th>
              <th class="r">발생 확률</th><th class="r">기대 건수</th><th class="r">실제</th><th>결과</th>
            </tr></thead>
            <tbody>
              <tr v-for="(r, i) in rows.slice(0, 1500)" :key="i">
                <td class="mono">{{ r.targetDate }}</td>
                <td class="faint">{{ r.sido }}</td><td>{{ r.sigungu }}</td>
                <td><GradeBadge :grade="r.predictedGrade" /></td>
                <td class="num r">{{ pct(r.occurProbability) }}</td>
                <td class="num r">{{ Number(r.expectedCount).toFixed(3) }}</td>
                <td class="num r strong">{{ r.actualCount }}</td>
                <td><span class="oc" :class="`oc--${r.outcome}`">{{ OUTCOME_KO[r.outcome] }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <p class="foot">전체 {{ rows.length.toLocaleString() }}건 중 1,500건까지 보여드립니다 · 예측을 다시 계산해도 이전 기록은 지우지 않고 그대로 남깁니다</p>
    </template>
  </section>
</template>

<style scoped>
.head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--sp-md); margin-bottom: var(--sp-md); flex-wrap: wrap; }

.matrix { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1px; margin-bottom: var(--sp-md); }
.mx { padding: var(--sp-md); background: var(--surface-1); border: 1px solid var(--hairline); border-top: 3px solid var(--ink-faint); display: flex; flex-direction: column; gap: 2px; }
.mx b { font-size: 30px; font-weight: 300; }
.mx em { font-style: normal; font-size: 10px; color: var(--ink-faint); font-family: var(--font-mono); }
.mx--hit { border-top-color: var(--g-low-ink); }
.mx--hit b { color: var(--g-low-ink); }
.mx--fa { border-top-color: var(--g-mid-ink); }
.mx--fa b { color: var(--g-mid-ink); }
.mx--miss { border-top-color: var(--g-critical-ink); }
.mx--miss b { color: var(--g-critical-ink); }

.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--sp-md); margin-bottom: var(--sp-md); }
.hint { margin: 4px 0 var(--sp-sm); font-size: 11px; color: var(--ink-subtle); line-height: 1.5; }
.dl { display: grid; grid-template-columns: auto 1fr; gap: 5px var(--sp-md); margin: 0; font-size: 13px; }
.dl dt { color: var(--ink-subtle); font-size: 12px; }
.dl dd { margin: 0; text-align: right; }
.big { font-size: 40px; font-weight: 300; margin: var(--sp-sm) 0 0; }
.verdict { margin: var(--sp-sm) 0 0; padding-top: var(--sp-xs); border-top: 1px solid var(--hairline); font-size: 11px; }
.verdict.pass { color: var(--g-low-ink); }
.verdict.fail { color: var(--g-high-ink); line-height: 1.55; }

/* 등급별 표 — 이 화면의 주 지표 */
.grades td, .grades th { padding: 7px var(--sp-xs); }
.grades .strong { font-weight: 600; }

/* '발생 없음이 많다' 는 오해를 푸는 설명 상자 */
.caveat { margin: var(--sp-sm) 0 0; padding: var(--sp-xs) var(--sp-sm);
          border-left: 3px solid var(--g-mid); background: color-mix(in srgb, var(--g-mid) var(--tint-soft), transparent);
          font-size: 11px; line-height: 1.6; color: var(--ink-muted); }
.caveat b { color: var(--g-mid-ink); }
.caveat b:first-child { display: block; margin-bottom: 3px; }
.caveat .warn { color: var(--g-critical-ink); }
.vs { margin-left: 6px; font-size: 10px; color: var(--ink-faint); }
.us { font-size: 10px; font-weight: 400; color: var(--ink-faint); font-family: var(--font-mono); }

.cal { position: relative; display: flex; align-items: flex-end; gap: 2px; height: 110px; padding-bottom: 16px; }
.cal__col { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; height: 100%; }
.cal__bar { width: 100%; background: var(--primary); min-height: 2px; }
.cal__col.bad .cal__bar { background: var(--g-high); }
.cal__x { position: absolute; bottom: 0; font-size: 9px; color: var(--ink-faint); }
.cal__base { position: absolute; left: 0; right: 0; bottom: calc(16px + 50%); border-top: 1px dashed var(--ink-faint); }

.pad0 { padding: 0; }
.tblwrap { max-height: 44vh; }
.strong { font-weight: 600; }
.faint { color: var(--ink-faint); font-size: 12px; }
.oc { font-size: 11px; padding: 1px 7px; letter-spacing: .32px; }
.oc--hit { color: var(--g-low-ink); background: color-mix(in srgb, var(--g-low) var(--tint), transparent); }
.oc--false_alarm { color: var(--g-mid-ink); background: color-mix(in srgb, var(--g-mid) var(--tint), transparent); }
.oc--miss { color: var(--g-critical-ink); background: color-mix(in srgb, var(--g-critical) var(--tint), transparent); }
.oc--correct_reject { color: var(--ink-faint); }
.foot { margin-top: var(--sp-sm); font-size: 11px; color: var(--ink-subtle); }
.err { border-left: 3px solid var(--g-critical); color: var(--g-critical-ink); }
.muted { color: var(--ink-subtle); }
</style>
