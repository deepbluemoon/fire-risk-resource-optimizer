<script setup lang="ts">
/** US1 시나리오 2·3·4 — 상세. FR-015 기여요인·관측지점·신뢰도, FR-023/SC-005 입력 재구성
 *  US2 확산 지표 · US3 원인 후보를 같은 화면에서 본다. */
import { computed, onMounted, ref, watch } from 'vue';
import { RouterLink } from 'vue-router';
import { api, pct, num, modelLabel, everyHowOften,
         type CauseResponse, type RiskDetail } from '../services/api';
import GradeBadge from '../components/GradeBadge.vue';
import RegionMap from '../components/RegionMap.vue';
import WeatherNow from '../components/WeatherNow.vue';

const p = defineProps<{ regionCd: string; date: string }>();

const d = ref<RiskDetail | null>(null);
const cause = ref<CauseResponse | null>(null);
const loading = ref(true);
const err = ref<string | null>(null);
const showInputs = ref(false);

async function load() {
  loading.value = true; err.value = null;
  try {
    const cd = decodeURIComponent(p.regionCd);
    const [detail, c] = await Promise.allSettled([
      api.riskDetail(cd, p.date, true), api.cause(cd, p.date),
    ]);
    if (detail.status === 'fulfilled') d.value = detail.value;
    else throw new Error((detail.reason as Error).message);
    cause.value = c.status === 'fulfilled' ? c.value
      : { items: [], model: { loaded: false, passed: false, caveat: null, metrics: null },
          unavailable: '조회 실패' };
  } catch (e: any) { err.value = e.message; }
  finally { loading.value = false; }
}
onMounted(load);
watch(() => [p.regionCd, p.date], load);

/** 내부 상태값을 사용자가 읽을 수 있는 말로 바꾼다. */
const confidenceKo = (c: string | null | undefined) =>
  ({ 정상: '보통', 대리지점: '조금 낮음 (이웃 지역 날씨 사용)', 폴백: '낮음 (날씨 정보 없음)' }
    [c ?? ''] ?? (c || '—'));

/** 사용한 모델을 '무엇을 하는지 (코드)' 로 보여준다. 코드만 쓰면 사용자가 알 수 없다. */
const usedModels = computed(() => {
  const v = d.value?.modelVersions ?? {};
  const names = Object.values(v).filter(Boolean).map((x) => modelLabel(String(x)));
  return [...new Set(names)].join(' · ') || '—';
});

/** M3 가 합격하지 못한 모델일 때 화면 전체를 '불안전' 으로 표시한다.
 *  reliability 가 없는 구버전 응답도 passed=false 면 불안전으로 본다. */
const causeUnsafe = computed(() =>
  cause.value?.model?.loaded === true && cause.value?.model?.passed === false);
const causeDetail = computed(() => cause.value?.model?.caveatDetail ?? null);

/** US3 시나리오 2 — 등급이 낮은데 원인을 단정적으로 지목하지 않는다 */
const causeTone = (grade: string | null | undefined) =>
  grade === '높음' || grade === '매우높음'
    ? '오늘 이 지역에서 평소보다 눈에 띄게 늘어난 화재 원인입니다.'
    : '오늘은 위험 등급이 낮아, 아래 내용은 참고만 해주세요.';
</script>

<template>
  <section>
    <RouterLink to="/" class="back">← 위험도 목록</RouterLink>

    <div v-if="err" class="tile err">{{ err }}</div>
    <div v-else-if="loading" class="tile muted">불러오는 중…</div>

    <template v-else-if="d">
      <header class="head">
        <div>
          <p class="eyebrow">{{ d.sido }} · {{ d.targetDate }}</p>
          <h1>{{ d.sigungu }}</h1>
          <p class="sub">
            <GradeBadge :grade="d.riskGrade" :status="d.dataStatus" />
            <span v-if="d.clusterLabel" class="chip">{{ d.clusterLabel }}</span>
            <span class="chip chip--rank">관할 {{ d.riskRank }}위</span>
          </p>
        </div>
        <div class="kpis">
          <div class="kpi">
            <span class="eyebrow">화재가 날 가능성</span>
            <b class="num">{{ pct(d.occurProbability) }}</b>
            <em>이 날 하루 중 한 건이라도 날 가능성</em>
          </div>
          <div class="kpi">
            <span class="eyebrow">예상 화재 건수</span>
            <b class="num">{{ num(d.expectedCount) }}</b>
            <em>
              <template v-if="everyHowOften(d.expectedCount)">
                {{ everyHowOften(d.expectedCount) }} ·
              </template>
              이 날 하루 기준
            </em>
          </div>
          <div class="kpi">
            <span class="eyebrow">대응 부담</span>
            <b class="num">{{ num(d.expectedDamageLoad) }}</b>
            <em>
              <template v-if="d.riskRank">전국 {{ d.riskRank }}위 · </template>
              자주 나고 · 크게 번지고 · 오래 걸릴수록 커집니다
            </em>
          </div>
        </div>
      </header>

      <!-- 이 지역의 지금 날씨. 등급 옆에 두면 '왜 오늘 이 등급인지' 가 바로 읽힌다. -->
      <WeatherNow v-if="d.lat != null && d.lon != null"
                  :date="p.date" :lat="d.lat" :lon="d.lon"
                  :place-name="`${d.sigungu} 지금 날씨`" />

      <div class="grid">
        <!-- 기여 요인: 문장 3개 (US1 시나리오 2) -->
        <div class="tile t-why">
          <h3>왜 이 등급인가요</h3>
          <p class="hint">오늘 이 지역의 위험도를 올리거나 내린 조건입니다.</p>
          <ul class="factors">
            <li v-for="(f, i) in d.factors" :key="i" :class="f.direction === '상승' ? 'up' : 'down'">
              <span class="factors__dir" aria-hidden="true">{{ f.direction === '상승' ? '▲' : '▼' }}</span>
              <div>
                <p class="factors__s">{{ f.sentenceKo }}</p>
                <p class="factors__m">
                  {{ f.direction === '상승' ? '위험을 높이는 조건' : '위험을 낮추는 조건' }}
                </p>
              </div>
            </li>
            <li v-if="!d.factors.length" class="muted">
              오늘은 위험도를 크게 바꾼 조건이 없습니다.
            </li>
          </ul>
        </div>

        <!-- 신뢰도 (US1 시나리오 3·4) -->
        <div class="tile t-trust">
          <h3>이 예측을 얼마나 믿을 수 있나요</h3>
          <dl class="dl">
            <dt>오늘 날씨 반영</dt>
            <dd :class="d.dataStatus !== '정상' && 'warn'">
              {{ d.dataStatus === '정상' ? '반영됨' : '반영 안 됨' }}
            </dd>
            <dt>예측 신뢰도</dt>
            <dd :class="d.confidence !== '정상' && 'warn'">{{ confidenceKo(d.confidence) }}</dd>
            <dt>날씨 관측소</dt>
            <dd :class="d.stationAssignMethod !== '관내' && 'warn'">
              {{ d.stationAssignMethod === '관내' ? '이 지역 안에 있음' : '이웃 지역 관측소 사용' }}
            </dd>
            <dt>사용한 예측 모델</dt>
            <dd>{{ usedModels }}</dd>
          </dl>
          <p v-if="d.stationAssignMethod !== '관내'" class="warnbox">
            이 지역에는 기상 관측소가 없어 가까운 다른 지역의 날씨를 대신 썼습니다.
            그만큼 예측이 덜 정확할 수 있습니다.
          </p>
        </div>

        <RegionMap :date="p.date" :region-cd="p.regionCd" />

        <!-- 확산 (US2) -->
        <div class="tile t-spread">
          <h3>불이 나면 얼마나 커질까요</h3>
          <p class="hint">
            피해 금액이 아니라 '얼마나 번지고 얼마나 오래 걸리는지'로 봅니다.
            금액으로 보면 땅값이 비싼 도심이 늘 위험해 보이기 때문입니다.
          </p>
          <dl class="dl">
            <dt>불이 번질 가능성</dt>
            <dd>
              <b class="num big2" :class="d.spreadProbability != null && d.spreadProbability > 0.297 && 'hot'">
                {{ d.spreadProbability != null ? pct(d.spreadProbability) : '알 수 없음' }}
              </b>
              <span v-if="d.spreadProbability != null" class="vs">전국 평균은 30%</span>
            </dd>
            <dt>
              진화가 길어질 가능성
              <span v-if="d.longburnProbability != null" class="badge-ref"
                    title="참고용 지표입니다">참고용</span>
            </dt>
            <dd class="num" :class="d.longburnProbability == null && 'faint'">
              {{ d.longburnProbability != null ? pct(d.longburnProbability) : '알 수 없음' }}
              <span v-if="d.longburnProbability != null" class="vs">소방차가 2시간 넘게 묶이는 화재</span>
            </dd>
            <dt>대응 부담</dt>
            <dd class="num">
              {{ num(d.expectedDamageLoad) }}
              <span class="vs">자주 나고 · 크게 번지고 · 오래 걸릴수록 높아집니다</span>
            </dd>
          </dl>
          <p v-if="d.longburnProbability != null" class="caveat caveat-ref">
            <b>'진화가 길어질 가능성'은 참고용입니다</b>
            소방차가 2시간 넘게 묶이는 화재가 오늘 이 지역에서 날 가능성입니다.
            <b class="warn">아직 정확도가 충분하지 않아 참고 자료로만 쓰시는 것이 좋습니다.</b>
          </p>
          <p v-if="d.spreadProbability == null" class="muted small">
            이 날짜는 날씨 정보가 없어 지역·계절 평균값으로만 계산했습니다.
            그래서 번짐 예측이 나오지 않습니다.
          </p>
        </div>

        <!-- 원인 이상 신호 (US3) — 순위는 보여주지 않는다. M3 는 순위에서 상수
             예측기에 졌으므로 정보가 없고, 보여주면 사용자가 근거로 삼는다.
             평시 대비 배율이 기준을 넘은 것만 남긴다. -->
        <div class="tile t-cause">
          <h3>
            평소와 다른 화재 원인
            <span v-if="causeUnsafe" class="badge-ref"
                  title="참고용 지표입니다">참고용</span>
          </h3>
          <p class="hint">{{ causeTone(d.riskGrade) }}</p>

          <ul v-if="cause?.items?.length" class="signals">
            <li v-for="c in cause.items" :key="c.causeClass">
              <span class="sig-name">{{ c.causeClass }}</span>
              <b class="num hot">평소의 {{ Number(c.liftVsBase).toFixed(1) }}배</b>
            </li>
          </ul>
          <p v-else class="muted small">{{ cause?.unavailable ?? '데이터 없음' }}</p>

          <div v-if="causeDetail" class="caveat caveat-ref">
            <b>{{ causeDetail.headline }}</b>
            <p class="lead">{{ causeDetail.lead }}</p>
            <ul v-if="causeDetail.reasons?.length">
              <li v-for="(r, i) in causeDetail.reasons" :key="i">{{ r }}</li>
            </ul>
            <p v-if="causeDetail.warning" class="warn">{{ causeDetail.warning }}</p>
            <p v-if="causeDetail.trustworthy" class="trust">{{ causeDetail.trustworthy }}</p>
          </div>
          <p v-else-if="cause?.model?.caveat" class="caveat caveat-ref">
            <b>참고용 지표</b> {{ cause.model.caveat }}
          </p>
        </div>
      </div>

      <!-- SC-005 재구성 -->
      <div class="tile">
        <div class="row">
          <h3>이 예측에 쓰인 값</h3>
          <button class="btn btn--sm" @click="showInputs = !showInputs">
            {{ showInputs ? '접기' : '모두 보기' }}
          </button>
        </div>
        <p class="hint">
          예측에 실제로 들어간 값을 그대로 보여드립니다.
          <b>A</b>는 이미 확정된 값(지난 날씨·지역 정보), <b>B</b>는 오늘 날씨 예보로 채운 값입니다.
        </p>
        <div v-if="showInputs && d.inputs" class="inputs">
          <div v-for="(v, k) in d.inputs.features" :key="k" class="inputs__row">
            <span class="inputs__k">{{ k }}</span>
            <span class="inputs__v mono">{{ v }}</span>
            <span class="inputs__g" :class="`g-${d.inputs.featureGrade?.[k as string] ?? '-'}`">
              {{ d.inputs.featureGrade?.[k as string] ?? '' }}
            </span>
          </div>
        </div>
        <p v-else-if="showInputs" class="muted small">입력 스냅샷이 저장되지 않았습니다.</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.back { display: inline-block; margin-bottom: var(--sp-sm); font-size: 12px; color: var(--ink-subtle); }
.back:hover { color: var(--primary); }
.head { display: flex; justify-content: space-between; align-items: flex-end; gap: var(--sp-lg); flex-wrap: wrap; margin-bottom: var(--sp-md); }
.sub { display: flex; align-items: center; gap: var(--sp-xs); margin: var(--sp-xs) 0 0; }
/* 밝은 화면에서는 회색 알약 대신 선으로 두른다 */
.chip { font-size: 11px; padding: 1px 8px; letter-spacing: .32px;
        background: var(--inset); border: 1px solid var(--inset-line);
        color: var(--ink-muted); }
:root[data-theme='dark'] .chip { background: var(--surface-2); border-color: transparent; }
.chip--rank { font-family: var(--font-mono); }

.kpis { display: flex; gap: 1px; }
.kpi { display: flex; flex-direction: column; gap: 2px; padding: var(--sp-sm) var(--sp-md); background: var(--surface-1); border: 1px solid var(--hairline); min-width: 128px; }
.kpi b { font-size: 26px; font-weight: 300; }
.kpi em { font-style: normal; font-size: 10px; color: var(--ink-faint); font-family: var(--font-mono); }

/* 3열 배치 — 왼쪽 두 열은 타일을 세로로 쌓고, 지도는 오른쪽 한 열을 통째로 쓴다.
   지도가 세로로 길기 때문에, 옆 열을 두 칸씩 쌓아야 아래 끝이 나란히 맞는다.
     [왜 이 등급   ] [불이 나면 커질까] [        ]
     [얼마나 믿을까] [평소와 다른 원인] [  지도  ]
   내용이 길어지는 타일은 늘어나지 않고 그 안에서 스크롤한다(.caveat). */
.grid {
  display: grid; gap: var(--sp-md); margin-bottom: var(--sp-md);
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-template-areas:
    'why    spread map'
    'trust  cause  map';
  align-items: stretch;
}
.grid > * { min-width: 0; display: flex; flex-direction: column; }
/* '불이 나면 얼마나 커질까요' 와 '이 예측을 얼마나 믿을 수 있나요' 의 자리를 맞바꿨다.
   왼쪽 열은 '왜 이 등급인가 → 얼마나 커지나' 로 위험 이야기를 이어가고,
   신뢰도는 오른쪽 위로 올려 원인 신호와 함께 둔다. */
.grid > .t-why    { grid-area: why; }
.grid > .t-spread { grid-area: trust; }   /* 커질까 → 왼쪽 아래 */
.grid > .t-trust  { grid-area: spread; }  /* 믿을 수 있나 → 가운데 위 */
.grid > .t-cause  { grid-area: cause; }
.grid > .map      { grid-area: map; }

/* 좁아지면 2열 → 지도는 아래로 내려 폭을 채운다 */
@media (max-width: 1200px) {
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-template-areas: 'why spread' 'trust cause' 'map map';
  }
}
@media (max-width: 640px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas: 'why' 'trust' 'spread' 'cause' 'map';
  }
}
.tile h3 { margin-bottom: 4px; }
.hint { margin: 0 0 var(--sp-sm); font-size: 11px; color: var(--ink-subtle); line-height: 1.5; }

/* 좌측 두 타일은 항목 수가 적어 아래가 크게 비었다. 안쪽 간격을 줄이고
   목록을 위아래로 벌려 빈 공간을 없앤다. */
.t-why .factors, .t-trust .dl { flex: 1; }
.t-why .factors { justify-content: space-evenly; gap: var(--sp-xs); }
.t-why .hint, .t-trust .hint { margin-bottom: var(--sp-xs); }
.t-trust .dl { align-content: space-evenly; row-gap: var(--sp-xs); }
.t-trust .warnbox { margin-top: auto; }

.factors { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-sm); }
.factors li { display: flex; gap: var(--sp-sm); padding-left: var(--sp-xs); border-left: 2px solid var(--hairline); }
.factors li.up { border-left-color: var(--g-high-ink); }
.factors li.down { border-left-color: var(--primary); }
.factors__dir { font-size: 10px; color: var(--ink-subtle); padding-top: 3px; }
.factors__s { margin: 0; font-size: 13px; line-height: 1.5; }
.factors__m { margin: 2px 0 0; display: flex; gap: var(--sp-xs); font-size: 10px; color: var(--ink-faint); }

.dl { display: grid; grid-template-columns: auto 1fr; gap: 6px var(--sp-md); margin: 0; font-size: 13px; }
.dl dt { color: var(--ink-subtle); font-size: 12px; }
.dl dd { margin: 0; text-align: right; }
.warnbox { margin: var(--sp-sm) 0 0; padding: var(--sp-xs) var(--sp-sm); border-left: 3px solid var(--g-mid); background: color-mix(in srgb, var(--g-mid) var(--tint-soft), transparent); font-size: 11px; line-height: 1.5; }

.mini th, .mini td { font-size: 12px; }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-md); }
.inputs { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 1px; margin-top: var(--sp-sm); }
.inputs__row { display: flex; align-items: center; gap: var(--sp-xs); padding: 6px var(--sp-xs);
               background: var(--inset); border-bottom: 1px solid var(--inset-line); font-size: 12px; }
.inputs__k { color: var(--ink-muted); }
.inputs__v { margin-left: auto; }
.inputs__g { width: 16px; text-align: center; font-size: 10px; font-weight: 600; }
.g-A { color: var(--g-low-ink); }
.g-B { color: var(--g-mid-ink); }

.warn { color: var(--g-mid-ink); }
.big2 { font-size: 20px; font-weight: 300; }
.hot { color: var(--g-high-ink); }
.vs { margin-left: 6px; font-size: 10px; color: var(--ink-faint); font-family: var(--font-mono); }
.flag { margin-left: 6px; font-size: 10px; padding: 0 5px; color: var(--g-high-ink);
        background: color-mix(in srgb, var(--g-high) var(--tint), transparent); letter-spacing: .32px; }
tr.anomaly td { background: color-mix(in srgb, var(--g-high) var(--tint-soft), transparent); }
.caveat { margin: var(--sp-sm) 0 0; padding: var(--sp-xs) var(--sp-sm);
          border-left: 3px solid var(--g-mid); background: color-mix(in srgb, var(--g-mid) var(--tint-soft), transparent);
          font-size: 11px; line-height: 1.55; color: var(--ink-muted); }
.caveat b { color: var(--g-mid-ink); display: block; margin-bottom: 2px; }

/* 참고용(주황) — 지역평균은 넘지만 절대 정확도가 낮은 지표.
   불합격(빨강)과 구분한다. 둘을 같은 색으로 칠하면 경고가 흔해져
   정말 위험한 쪽까지 무시하게 된다. */
.badge-ref { margin-left: 6px; padding: 1px 5px; font-size: 10px; font-weight: 600;
             letter-spacing: .3px; color: var(--g-mid-ink); vertical-align: middle;
             background: color-mix(in srgb, var(--g-mid) var(--tint), transparent); }
.caveat-ref { border-left-color: var(--g-mid-ink); }
.caveat-ref b { color: var(--g-mid-ink); }
/* 긴 안내는 타일을 늘리지 않고 여기서만 스크롤한다 — 타일이 커지면 같은 행의
   옆 타일에 빈 공간이 그만큼 생긴다. 스크롤이 생겼다는 것은 아래 그림자로 알린다. */
.caveat {
  max-height: 132px; overflow-y: auto; overscroll-behavior: contain;
  margin-top: auto;   /* 타일 안에서 항상 아래쪽에 붙는다 */
}
.caveat::-webkit-scrollbar { width: 8px; }
.caveat::-webkit-scrollbar-thumb { background: var(--surface-2); }
.caveat::-webkit-scrollbar-track { background: transparent; }

/* 이상 신호 목록 — 순위표를 대신한다 */
.signals { margin: 0; padding: 0; list-style: none; }
/* 밝은 화면에서는 카드와 같은 흰 바탕에 선으로만 나눈다 — 회색 블록을 쌓으면
   흰 카드 위에 덩어리가 얹힌 꼴이 되어 답답하다. */
.signals li { display: flex; align-items: baseline; gap: var(--sp-xs);
              padding: 7px var(--sp-xs); background: var(--inset);
              border-bottom: 1px solid var(--inset-line); }
.signals li:last-child { border-bottom: 0; }
.sig-name { flex: 1; }
.signals .hot { font-size: 15px; }

.caveat-ref .lead { margin: 2px 0 4px; }
.caveat-ref ul { margin: 0 0 4px; padding-left: 15px; list-style: disc; }
.caveat-ref li { margin-bottom: 2px; display: list-item; padding: 0; background: none; }
.caveat-ref .warn { margin: 4px 0 2px; font-weight: 600; }
.caveat-ref .trust { margin: 0; color: var(--ink-muted); }

.muted { color: var(--ink-subtle); }
.small { font-size: 11px; }
.err { border-left: 3px solid var(--g-critical); color: var(--g-critical-ink); }
</style>
