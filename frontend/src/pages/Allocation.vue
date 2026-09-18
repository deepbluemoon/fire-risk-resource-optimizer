<script setup lang="ts">
/** US4 — 위험도 기반 인력 배분안 산출 및 조정
 *  FR-016 부족/잉여 구분 · FR-017 즉시 반영 · FR-036 제약 위반 거부 · FR-039 이력 보존
 *
 *  이 화면이 보여주는 것은 "각 시군구에 오늘 몇 명을 두는 것이 좋은가" 다.
 *  계산은 세 걸음이다.
 *    1) 대응 부담  d = 예상 건수 × (1+번질 가능성) × (1+길어질 가능성)
 *    2) 목표 인원  need = 전체 인력 × d / Σd          ← 위험에 비례한 몫
 *    3) 권장 배치  x   = 시도 안에서 need 를 최대한 채우도록 다시 나눈 결과
 *  인력은 시도 경계를 넘지 못하므로 3)은 시도마다 따로 푼다.
 */
import { computed, onMounted, ref, watch } from 'vue';
import { api, pct, num, today, dateTimeKo } from '../services/api';
import { useAppStore } from '../stores/app';
import GradeBadge from '../components/GradeBadge.vue';
import { scoreMatch } from '../util/hangul';

const app = useAppStore();
const date = ref(today());
const proposal = ref<any>(null);
const adjustments = ref<any[]>([]);
const loading = ref(true);
const err = ref<string | null>(null);
const saving = ref(false);
const violations = ref<any[]>([]);
const saved = ref<string | null>(null);

const edits = ref<Record<string, number | null>>({});
const authorId = ref('');
const authorRole = ref<'상황실' | '지휘' | '관리자' | '정책'>('지휘');
const reason = ref('');

/** 정책값 — 데이터가 아니라 사람이 정하는 값이다.
 *
 *  α(최소 유지 비율)만은 "모르니까 0" 으로 둘 수 없다. α=0 으로 풀면 부담이 작은
 *  지역을 통째로 비워 큰 지역에 몰아주는 것이 수식상 최적이 되어, 250곳 중 31곳이
 *  0명이 되는 배치가 나온다(성남시분당구 397명 → 0명). 그래서 기본값을 0.7 로 두고
 *  화면에 드러낸다. 이 값은 근거가 있는 추정치가 아니라 **운영자가 책임지고 고르는
 *  값**이므로 숨기면 안 된다. */
const alpha = ref(0.7);
const delta = ref<number | '무제한'>(50);

const ALPHA_OPTS = [
  { v: 0.5, t: '50%', d: '많이 옮길 수 있음' },
  { v: 0.6, t: '60%', d: '' },
  { v: 0.7, t: '70%', d: '권장' },
  { v: 0.8, t: '80%', d: '' },
  { v: 0.9, t: '90%', d: '거의 안 옮김' },
];
const DELTA_OPTS: Array<{ v: number | '무제한'; t: string }> = [
  { v: 10, t: '10명' }, { v: 20, t: '20명' }, { v: 30, t: '30명' },
  { v: 50, t: '50명' }, { v: 100, t: '100명' }, { v: '무제한', t: '무제한' },
];

async function load() {
  loading.value = true; err.value = null; violations.value = []; saved.value = null;
  try {
    proposal.value = await api.proposal(date.value, { alpha: alpha.value, delta: delta.value });
    edits.value = {};
    adjustments.value = await api.adjustments();
  } catch (e: any) { err.value = e.message; proposal.value = null; }
  finally { loading.value = false; }
}
onMounted(async () => { await app.bootstrap(); date.value = app.date; load(); });

/** 정책값을 바꾸면 곧바로 다시 계산한다. 계산은 서버에서 정렬 한 번과 반복문
 *  하나로 끝나므로(수십 ms) 별도의 '적용' 버튼을 두지 않는다. */
watch([alpha, delta], load);

const items = computed(() => proposal.value?.items ?? []);
const isAbsolute = computed(() => proposal.value?.mode === 'absolute');

/** 지역 검색 — 메인 화면과 같은 규칙(초성 인식·조합 완료 후 한 번만 거르기).
 *
 *  거른 결과는 **표에만** 쓴다. 위쪽 요약값은 전국 250행 전체를 봐야 한다.
 *  검색은 보는 범위를 좁히는 것이지 계산의 모집단을 바꾸는 것이 아니다. */
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

const visible = computed(() => {
  const s = q.value.trim();
  if (!s) return items.value;
  const hit: Array<{ it: any; score: number }> = [];
  for (const it of items.value) {
    const bySigungu = scoreMatch(it.sigungu ?? '', s);
    if (bySigungu !== null) { hit.push({ it, score: bySigungu }); continue; }
    const bySido = scoreMatch(it.sido ?? '', s);
    if (bySido !== null) hit.push({ it, score: bySido + 10 });
  }
  return hit.sort((a, b) => a.score - b.score).map((h) => h.it);
});

/** FR-017 — 내가 고친 값을 넣었을 때 남는 위험을 즉시 다시 잰다.
 *  권장 배치 x 자리에 고친 값을 끼워 넣고 같은 자(Σ d·미충족)로 잰다. */
const liveUnmet = computed(() => {
  if (!isAbsolute.value || !items.value.length) return null;
  return items.value.reduce((s: number, i: any) => {
    const head = edits.value[i.regionCd] ?? i.recommendedHeadcount ?? 0;
    return s + Number(i.demandScore) * Math.max(0, Number(i.needHeadcount) - Number(head));
  }, 0);
});
const dirty = computed(() => Object.values(edits.value).some((v) => v != null));

/** 고친 값의 총합이 현재 총원과 맞는지. 사람을 새로 만들어내면 안 된다. */
const editedTotal = computed(() => {
  if (!isAbsolute.value) return null;
  return items.value.reduce((s: number, i: any) =>
    s + Number(edits.value[i.regionCd] ?? i.recommendedHeadcount ?? 0), 0);
});

const fmt = (v: any, dp = 0) => (v == null ? '—' : num(Number(v), dp));
const signed = (v: any) => {
  if (v == null) return '—';
  const n = Number(v);
  if (Math.abs(n) < 0.5) return '0';
  return `${n > 0 ? '+' : '−'}${num(Math.abs(n), 0)}`;
};

async function save() {
  saving.value = true; violations.value = []; saved.value = null;
  try {
    const payload = {
      targetDate: date.value,
      policy: { alpha: alpha.value, delta: delta.value },
      authorId: authorId.value || '미기입', authorRole: authorRole.value,
      reason: reason.value,
      items: Object.entries(edits.value).filter(([, v]) => v != null)
        .map(([regionCd, v]) => ({ regionCd, adjustedHeadcount: Number(v) })),
    };
    const r = await api.saveAdjustment(payload);
    saved.value = `저장했습니다 (기록 번호 ${r.adjustmentId}).`;
    adjustments.value = await api.adjustments();
    edits.value = {}; reason.value = '';
  } catch (e: any) {
    if (e.details?.violations) violations.value = e.details.violations;
    else err.value = e.message;
  } finally { saving.value = false; }
}
</script>

<template>
  <section>
    <header class="head">
      <div>
        <p class="eyebrow">인력 배분</p>
        <h1>위험도에 맞춘 인력 배분안</h1>
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
        <label class="lbl">대상일<input v-model="date" type="date" class="field" @change="load" /></label>
      </div>
    </header>

    <div v-if="err" class="tile err">{{ err }}</div>
    <div v-else-if="loading && !proposal" class="tile muted">불러오는 중…</div>

    <template v-else-if="proposal">
      <!-- 정책값 — 계산 결과를 바꾸는 사람의 선택. 숨기지 않는다. -->
      <div v-if="proposal.mode !== 'unavailable'" class="policy">
        <div class="policy__g">
          <span class="policy__k">한 지역에 최소한 남길 인원
            <em>지금 인원 대비</em>
          </span>
          <div class="seg">
            <button v-for="o in ALPHA_OPTS" :key="o.v" class="seg__b"
                    :class="alpha === o.v && 'is-on'" @click="alpha = o.v">
              {{ o.t }}<i v-if="o.d">{{ o.d }}</i>
            </button>
          </div>
        </div>
        <div class="policy__g">
          <span class="policy__k">한 지역에서 하루에 바꿀 수 있는 인원
            <em>많이 허용할수록 개선폭이 커집니다</em>
          </span>
          <div class="seg">
            <button v-for="o in DELTA_OPTS" :key="String(o.v)" class="seg__b"
                    :class="delta === o.v && 'is-on'" @click="delta = o.v">{{ o.t }}</button>
          </div>
        </div>
        <p class="policy__note">
          이 두 값은 <b>데이터가 아니라 운영 판단</b>입니다. 소방관서의 실제 최소 잔류 규정이
          아직 등록되지 않아 기본값(70% · 50명)을 두었습니다.
          <b>최소 인원을 0%로 두면</b> 부담이 작은 지역을 통째로 비워 큰 지역에 몰아주는 것이
          계산상 가장 좋은 답이 되어, 250곳 중 31곳이 0명이 되는 배치가 나옵니다.
          그래서 이 값은 비워둘 수 없습니다.
        </p>
      </div>

      <!-- 대응 부담을 계산할 수 없는 날 — 조용히 0 으로 메우지 않고 그대로 말한다 -->
      <div v-if="proposal.mode === 'unavailable'" class="tile blocked">
        <b>오늘은 배분안을 낼 수 없습니다</b>
        <p>{{ proposal.reason }}</p>
        <p class="cm">
          대응 부담은 <b>예상 건수 × (1+번질 가능성) × (1+길어질 가능성)</b> 으로 구합니다.
          번짐·장기화 예측이 없으면 이 값이 성립하지 않습니다.
          건수만으로 대신 계산할 수도 있지만, 그러면 부담의 상당 부분이 빠진 값이
          같은 이름으로 나가게 되므로 그렇게 하지 않습니다.
        </p>
        <p class="cm">
          {{ proposal.total }}곳 중 {{ proposal.missingDemand }}곳이 결측입니다 ·
          다른 날짜를 고르시면 배분안을 보실 수 있습니다.
        </p>
      </div>

      <div v-else-if="!isAbsolute" class="notice">
        <b>인원 수가 아니라 비율로만 제시합니다</b>
        일부 지역의 현재 인력을 알 수 없어 인원 수를 계산할 수 없습니다.
        어디에 몇 %를 배치할지만 알려드립니다.
      </div>
      <div v-else class="notice">
        <b>현재 인원은 공표 정원을 시군구로 나눈 추정치입니다</b>
        소방청 시도본부별 정원을 관할 인구에 비례해 시군구로 나눈 값입니다
        (전국 {{ fmt(proposal.totalStaff) }}명).
        각 소방관서의 실제 근무 인원이 등록되면 그 값으로 바뀝니다.
        <b>이 화면은 인력을 자동으로 옮기지 않습니다.</b>
      </div>

      <div v-if="proposal.mode !== 'unavailable'" class="stats">
        <div class="stat">
          <span class="eyebrow">지금 배치대로면</span>
          <b class="num">{{ num(proposal.unmetRiskBefore, 1) }}</b>
          <i>남는 위험 · 낮을수록 좋음</i>
        </div>
        <div class="stat stat--good">
          <span class="eyebrow">권장안대로 하면</span>
          <b class="num">{{ num(proposal.unmetRiskAfter, 1) }}</b>
          <i>{{ proposal.improvementVsNow != null ? `${pct(proposal.improvementVsNow)} 줄어듦` : '—' }}</i>
        </div>
        <div v-if="liveUnmet != null && dirty" class="stat stat--edit">
          <span class="eyebrow">내가 고친 안대로면</span>
          <b class="num">{{ num(liveUnmet, 1) }}</b>
          <i>총원 {{ fmt(editedTotal) }}명 / {{ fmt(proposal.totalStaff) }}명</i>
        </div>
        <div class="stat">
          <span class="eyebrow">옮겨야 하는 인원</span>
          <b class="num">{{ fmt(proposal.movedTotal) }}</b>
          <i>전체의 {{ pct(proposal.movedTotal / proposal.totalStaff, 1) }}</i>
        </div>
        <div class="stat">
          <span class="eyebrow">목표를 채운 시도</span>
          <b class="num">{{ proposal.sidoZero }} <small>/ {{ proposal.perSido.length }}</small></b>
          <i>남는 부족 {{ fmt(proposal.unmetTotal) }}명</i>
        </div>
      </div>

      <div v-if="violations.length" class="tile viol">
        <b>최소 인원 조건에 맞지 않아 저장하지 못했습니다</b>
        <ul>
          <li v-for="(v, i) in violations" :key="i">
            {{ v.regionCd }} · {{ v.constraintType }} 한도는 {{ v.limit }}인데 {{ v.attempted }}을(를) 입력했습니다
          </li>
        </ul>
      </div>
      <div v-if="saved" class="tile ok">{{ saved }}</div>

      <div v-if="proposal.mode !== 'unavailable'" class="tile pad0">
        <p v-if="q.trim()" class="found">
          <template v-if="visible.length">
            <b>{{ visible.length }}</b>곳 — 위 요약값은 검색과 무관하게 전국 기준입니다
          </template>
          <template v-else>찾는 지역이 없습니다</template>
        </p>
        <div class="scroll tblwrap">
          <table class="data">
            <thead>
              <tr>
                <th>시도</th><th>시군구</th><th>등급</th>
                <th class="r" title="예상 화재 건수를 '손이 가는 정도'로 환산한 값입니다. 예상 건수 × (1+번질 가능성) × (1+길어질 가능성). 지역끼리 견주는 상대값입니다.">대응 부담</th>
                <th class="r" title="전체 인력을 대응 부담에 비례해 나눈 몫입니다. 시도 경계를 보지 않은 값이라 그대로 배치할 수는 없습니다.">목표 인원</th>
                <th class="r">현재 인원</th>
                <th class="r" title="시도별 총량을 지키면서 목표에 최대한 가깝게 다시 나눈 결과입니다.">권장 배치</th>
                <th class="r">증감</th>
                <th class="r" title="권장 배치로도 목표에 못 미친 인원. 시도 안에 인력이 모자라면 0이 되지 않습니다.">남는 부족</th>
                <th class="r">내가 고친 값</th><th>판단 근거</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="it in visible" :key="it.regionCd">
                <td class="faint">{{ it.sido }}</td>
                <td class="strong">{{ it.sigungu }}</td>
                <td><GradeBadge :grade="it.riskGrade" /></td>
                <td class="num r">{{ num(it.demandScore) }}</td>
                <td class="num r">{{ fmt(it.needHeadcount) }}</td>
                <td class="num r">{{ fmt(it.currentStaff) }}</td>
                <td class="num r strong">{{ fmt(it.recommendedHeadcount) }}</td>
                <td class="num r" :class="Number(it.changeHeadcount) > 0.5 ? 'up' : Number(it.changeHeadcount) < -0.5 ? 'down' : 'faint'">
                  {{ signed(it.changeHeadcount) }}
                </td>
                <td class="num r" :class="Number(it.unmet) > 0.5 && 'short'">{{ fmt(it.unmet) }}</td>
                <td class="r">
                  <input v-model.number="edits[it.regionCd]" type="number" min="0"
                         class="field field--n"
                         :placeholder="it.recommendedHeadcount == null ? '' : String(Math.round(it.recommendedHeadcount))" />
                </td>
                <td class="why" :title="(it.rationale ?? []).map((f: any) => f.sentenceKo).join('\n')">
                  {{ it.rationale?.[0]?.sentenceKo ?? '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 시도별 요약 — 남는 부족이 어디에 몰려 있는지가 이 모델의 결론이다 -->
      <div v-if="proposal.mode !== 'unavailable'" class="tile pad0">
        <h3 class="ph">시도별로 보면 <span>인력은 시도 경계를 넘지 못하므로 부족은 시도 안에서만 메울 수 있습니다</span></h3>
        <div class="scroll">
          <table class="data mini">
            <thead><tr>
              <th>시도</th><th class="r">시군구</th><th class="r">현재 인원</th>
              <th class="r">목표 인원</th><th class="r">차이</th>
              <th class="r">남는 부족</th><th class="r">옮기는 인원</th>
            </tr></thead>
            <tbody>
              <tr v-for="s in proposal.perSido" :key="s.sido">
                <td class="strong">{{ s.sido }}</td>
                <td class="num r faint">{{ s.n }}</td>
                <td class="num r">{{ fmt(s.currentStaff) }}</td>
                <td class="num r">{{ fmt(s.needHeadcount) }}</td>
                <td class="num r" :class="s.currentStaff - s.needHeadcount < -0.5 ? 'short' : 'faint'">
                  {{ signed(s.currentStaff - s.needHeadcount) }}
                </td>
                <td class="num r" :class="s.unmet > 0.5 ? 'short' : 'ok0'">{{ fmt(s.unmet) }}</td>
                <td class="num r faint">{{ fmt(s.moved) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="foot">
          <b>남는 부족</b>이 0 인 시도는 인력이 충분한데 배치만 어긋나 있었다는 뜻입니다 —
          사람을 더 뽑지 않고 다시 나누기만 해도 해결됩니다.
          0 이 아닌 시도는 <b>다시 나눠서는 풀리지 않는 부족</b>이고, 증원이나 시도 간 이동 허용이
          있어야 줄어듭니다.
        </p>
      </div>

      <div v-if="proposal.mode !== 'unavailable'" class="tile save">
        <h3>고친 내용 저장</h3>
        <p class="hint">
          권장안과 수정 내용, 그때 쓴 정책값(최소 유지 {{ pct(alpha, 0) }} · 하루 조정 상한 {{ delta === '무제한' ? '무제한' : `${delta}명` }})이
          모두 기록으로 남습니다. 누가 언제 어떤 예측과 어떤 기준을 보고 정했는지 나중에 확인할 수 있습니다.
        </p>
        <div class="save__row">
          <label class="lbl">작성자<input v-model="authorId" class="field" placeholder="이름 또는 ID" /></label>
          <label class="lbl">역할
            <select v-model="authorRole" class="field">
              <option>상황실</option><option>지휘</option><option>관리자</option><option>정책</option>
            </select>
          </label>
          <label class="lbl grow">고친 이유<input v-model="reason" class="field" placeholder="권장안을 왜 바꿨는지 적어주세요" /></label>
          <button class="btn btn--primary" :disabled="saving || !dirty || !reason" @click="save">
            {{ saving ? '저장 중…' : '고친 내용 저장' }}
          </button>
        </div>
        <p class="disclaimer">
          이 시스템은 인력을 <b>자동으로 옮기지 않습니다.</b> 최종 배치는 담당자가 결정합니다.
        </p>
      </div>

      <div v-if="adjustments.length" class="tile">
        <h3>지금까지 고친 기록</h3>
        <table class="data mini">
          <thead><tr><th class="r">#</th><th>대상일</th><th>작성자</th><th>역할</th><th>고친 이유</th><th>그때 기준</th><th class="r">고친 지역 수</th><th>저장 시각</th></tr></thead>
          <tbody>
            <tr v-for="a in adjustments" :key="a.adjustmentId">
              <td class="num r">{{ a.adjustmentId }}</td><td class="mono">{{ a.targetDate }}</td>
              <td>{{ a.authorId }}</td><td class="faint">{{ a.authorRole }}</td>
              <td class="why">{{ a.reason }}</td>
              <td class="faint mono">
                {{ a.policy ? `최소 ${Math.round(a.policy.alpha * 100)}% · ${a.policy.delta}명` : '—' }}
              </td>
              <td class="num r">{{ a.items?.length ?? 0 }}</td>
              <td class="faint mono">{{ dateTimeKo(a.createdAt) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* flex-wrap 이 없어 좁아지면 제목과 조작부가 서로를 짓눌렀다 */
.head { display: flex; align-items: flex-end; justify-content: space-between;
        gap: var(--sp-md); flex-wrap: wrap; margin-bottom: var(--sp-md); }

.policy {
  border: 1px solid var(--hairline); background: var(--surface-1);
  padding: var(--sp-sm) var(--sp-md); margin-bottom: var(--sp-md);
  display: flex; flex-wrap: wrap; gap: var(--sp-md) var(--sp-lg); align-items: flex-start;
}
.policy__g { display: flex; flex-direction: column; gap: 6px; }
.policy__k { font-size: 11px; color: var(--ink-muted); letter-spacing: .2px; }
.policy__k em { font-style: normal; color: var(--ink-faint); margin-left: 6px; }
.policy__note {
  flex: 1 1 100%; margin: 0; padding-top: var(--sp-xs);
  border-top: 1px solid var(--hairline);
  font-size: 11px; line-height: 1.6; color: var(--ink-subtle);
}
.policy__note b { color: var(--ink-muted); }

.seg { display: flex; }
.seg__b {
  appearance: none; border: 1px solid var(--hairline); border-right: 0;
  background: var(--surface-2); color: var(--ink-muted);
  padding: 5px 11px; font: inherit; font-size: 12px; cursor: pointer;
  display: flex; align-items: baseline; gap: 5px; white-space: nowrap;
}
.seg__b:last-child { border-right: 1px solid var(--hairline); }
.seg__b i { font-style: normal; font-size: 10px; color: var(--ink-faint); }
.seg__b:hover { background: var(--surface-1); }
.seg__b.is-on { background: var(--primary); border-color: var(--primary); color: #fff; }
.seg__b.is-on i { color: rgba(255, 255, 255, .78); }

.notice {
  padding: var(--sp-sm) var(--sp-md); margin-bottom: var(--sp-md);
  border-left: 3px solid var(--primary); background: color-mix(in srgb, var(--primary) var(--tint-soft), var(--surface-1));
  font-size: 12px; line-height: 1.6; color: var(--ink-muted);
}
.notice b { color: var(--ink); }

.stats { display: flex; gap: 1px; margin-bottom: var(--sp-md); flex-wrap: wrap; }
.stat { flex: 1 1 150px; padding: var(--sp-sm) var(--sp-md); background: var(--surface-1); border: 1px solid var(--hairline); display: flex; flex-direction: column; gap: 2px; }
.stat b { font-size: 22px; font-weight: 300; }
.stat b small { font-size: 13px; color: var(--ink-faint); }
.stat i { font-style: normal; font-size: 11px; color: var(--ink-subtle); }
.stat--good b { color: var(--g-low-ink); }
.stat--edit { border-color: var(--primary); }
.stat--edit b { color: var(--primary); }

.pad0 { padding: 0; }
.ph { margin: 0; padding: var(--sp-sm) var(--sp-md); border-bottom: 1px solid var(--hairline); font-size: 13px; }
.ph span { font-weight: 400; font-size: 11px; color: var(--ink-subtle); margin-left: 8px; }
.foot { margin: 0; padding: var(--sp-sm) var(--sp-md); border-top: 1px solid var(--hairline);
        font-size: 11px; line-height: 1.6; color: var(--ink-subtle); }
.foot b { color: var(--ink-muted); }
.tblwrap { max-height: 46vh; }
.strong { font-weight: 500; }
.faint { color: var(--ink-faint); font-size: 12px; }
.why { max-width: 240px; overflow: hidden; text-overflow: ellipsis; font-size: 11px; color: var(--ink-subtle); }
.field--n { width: 74px; text-align: right; height: 26px; }

.up { color: var(--g-low-ink); }
.down { color: var(--primary); }
.short { color: var(--g-critical-ink); }
.ok0 { color: var(--ink-faint); }

.save { margin-top: var(--sp-md); }
.save__row { display: flex; gap: var(--sp-sm); align-items: flex-end; flex-wrap: wrap; }
.hint { margin: 4px 0 var(--sp-sm); font-size: 11px; color: var(--ink-subtle); line-height: 1.5; }
.disclaimer { margin: var(--sp-sm) 0 0; padding-top: var(--sp-xs); border-top: 1px solid var(--hairline); font-size: 11px; color: var(--ink-subtle); }
.disclaimer b { color: var(--g-mid-ink); }

.viol { border-left: 3px solid var(--g-critical); margin-bottom: var(--sp-md); }
.viol b { color: var(--g-critical-ink); }
.viol ul { margin: var(--sp-xs) 0 0; padding-left: var(--sp-md); font-size: 12px; }
.ok { border-left: 3px solid var(--g-low); color: var(--g-low-ink); margin-bottom: var(--sp-md); }
.err { border-left: 3px solid var(--g-critical); color: var(--g-critical-ink); }
.muted { color: var(--ink-subtle); }
.mini th, .mini td { font-size: 12px; }
.found { margin: 0; padding: 10px 14px; border-bottom: 1px solid var(--hairline);
         font-size: 12px; color: var(--ink-subtle); }
.found b { color: var(--ink); font-variant-numeric: tabular-nums; }

.blocked { border-left: 3px solid var(--g-critical); margin-bottom: var(--sp-md); }
.blocked b { color: var(--g-critical-ink); }
.blocked p { margin: var(--sp-xs) 0 0; font-size: 12px; line-height: 1.65; color: var(--ink-muted); }
.blocked .cm { font-size: 11px; color: var(--ink-subtle); }
</style>
