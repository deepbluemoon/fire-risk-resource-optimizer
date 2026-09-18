<script setup lang="ts">
/** 전국 위험도 지도 — 시군구 경계를 위험 등급 색으로 칠한다.
 *
 *  경계 자료
 *    southkorea/southkorea-maps (KOSTAT 2013, 단순화본) 을 프로젝트용으로 정리해
 *    frontend/src/assets/korea-sigungu.json 에 뒀다. 199KB 라 번들에 넣어도 부담이
 *    없고, 외부 지도 타일을 부르지 않으므로 폐쇄망에서도 그대로 뜬다.
 *    2013 년 경계라 그 뒤 개편분(인천 미추홀구·부천시 구 폐지·청주 통합·군위군
 *    대구 편입)은 만들 때 맞춰뒀다. 청주시서원구만 폴리곤이 없어 점으로 표시한다.
 *
 *  확대/축소는 하지 않는다 — 전국이 한 화면에 다 들어오는 배율로 고정한다.
 */
import { computed, onMounted, ref } from 'vue';
import { api, pct, GRADE_COLOR } from '../services/api';
import geo from '../assets/korea-sigungu.json';

const p = defineProps<{ date: string; regionCd: string }>();

const rows = ref<any[]>([]);
const loading = ref(true);
const failed = ref(false);
const hover = ref<string | null>(null);

onMounted(async () => {
  try { rows.value = await api.mapPoints(p.date); }
  catch { failed.value = true; }
  finally { loading.value = false; }
});

const byRegion = computed(() => {
  const m: Record<string, any> = {};
  for (const r of rows.value) m[r.regionCd] = r;
  return m;
});

/** 경위도 → 화면 좌표.
 *  위경도를 그대로 쓰면 한반도가 옆으로 눌린다. 위도 36도 부근에서 경도 1도는
 *  위도 1도보다 약 19% 짧다. cos 로 보정해 실제 모양에 가깝게 만든다. */
const W = 320;
const H = 400;
const PAD = 8;
const K = Math.cos((36 * Math.PI) / 180);

const view = computed(() => {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const f of (geo as any).features) {
    for (const poly of f.geometry.coordinates) {
      for (const ring of poly) {
        for (const [lon, lat] of ring) {
          const x = lon * K;
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (lat < y0) y0 = lat; if (lat > y1) y1 = lat;
        }
      }
    }
  }
  const s = Math.min((W - PAD * 2) / (x1 - x0), (H - PAD * 2) / (y1 - y0));
  const ox = (W - (x1 - x0) * s) / 2;
  const oy = (H - (y1 - y0) * s) / 2;
  return {
    x: (lon: number) => ox + (lon * K - x0) * s,
    y: (lat: number) => H - oy - (lat - y0) * s,
  };
});

/** 폴리곤 하나를 SVG path 로. 구멍(내부 링)은 evenodd 로 처리된다. */
function toPath(coords: number[][][][]): string {
  const v = view.value;
  const out: string[] = [];
  for (const poly of coords) {
    for (const ring of poly) {
      if (ring.length < 3) continue;
      out.push(ring.map(([lon, lat], i) =>
        `${i ? 'L' : 'M'}${v.x(lon).toFixed(1)} ${v.y(lat).toFixed(1)}`).join('') + 'Z');
    }
  }
  return out.join('');
}

const shapes = computed(() => (geo as any).features.map((f: any) => {
  const r = byRegion.value[f.properties.regionCd];
  return {
    regionCd: f.properties.regionCd,
    sido: f.properties.sido,
    sigungu: f.properties.sigungu,
    d: toPath(f.geometry.coordinates),
    row: r ?? null,
    isSelf: f.properties.regionCd === p.regionCd,
    color: !r || r.dataStatus === '데이터없음'
      ? 'var(--g-none)' : GRADE_COLOR[r.riskGrade ?? ''] ?? 'var(--g-none)',
  };
}));

/** 맨 위에 다시 그릴 '지금 보고 있는 지역'. */
const selfShape = computed(() => shapes.value.find((x: any) => x.isSelf) ?? null);

/** 폴리곤이 없는 지역(청주시서원구)은 청사 좌표에 점으로 찍는다 */
const dots = computed(() => {
  const have = new Set((geo as any).features.map((f: any) => f.properties.regionCd));
  return rows.value
    .filter((r) => !have.has(r.regionCd) && r.lat != null && r.lon != null)
    .map((r) => ({
      ...r,
      cx: view.value.x(Number(r.lon)),
      cy: view.value.y(Number(r.lat)),
      isSelf: r.regionCd === p.regionCd,
      color: r.dataStatus === '데이터없음'
        ? 'var(--g-none)' : GRADE_COLOR[r.riskGrade ?? ''] ?? 'var(--g-none)',
    }));
});

const self = computed(() => byRegion.value[p.regionCd] ?? null);
const shown = computed(() => (hover.value ? byRegion.value[hover.value] : null) ?? self.value);
const shownName = computed(() => {
  const cd = hover.value ?? p.regionCd;
  const f = (geo as any).features.find((x: any) => x.properties.regionCd === cd);
  return f ? f.properties : (shown.value ? { sido: shown.value.sido, sigungu: shown.value.sigungu } : null);
});

const legend = computed(() => {
  const c: Record<string, number> = { '매우높음': 0, '높음': 0, '보통': 0, '낮음': 0 };
  for (const r of rows.value) if (r.riskGrade && r.riskGrade in c) c[r.riskGrade]++;
  return c;
});
</script>

<template>
  <div class="tile map">
    <h3>전국 어디쯤인가요</h3>
    <p class="hint">
      색은 그날의 위험 등급입니다. <b>테두리가 굵은 곳</b>이 지금 보고 계신 지역이고,
      다른 지역에 마우스를 올리면 그곳 값이 표시됩니다.
    </p>

    <p v-if="loading" class="muted small">지도를 불러오는 중…</p>
    <p v-else-if="failed" class="muted small">이 날짜는 지도를 표시할 자료가 없습니다.</p>

    <div v-else class="map__wrap">
      <svg :viewBox="`0 0 ${W} ${H}`" class="map__svg" role="img"
           :aria-label="`전국 시군구 위험도 지도`" @mouseleave="hover = null">
        <path v-for="s in shapes" :key="s.regionCd" :d="s.d"
              :fill="s.color" class="map__area"
              :class="{ 'is-self': s.isSelf, 'is-hover': hover === s.regionCd }"
              @mouseenter="hover = s.regionCd" />
        <!-- 지금 보고 있는 지역을 맨 위에 한 번 더 그린다.
             폴리곤은 지오 순서대로 그려지므로, 뒤에 오는 이웃이 이 지역의
             외곽선을 덮어 테두리가 끊겨 보였다. 흰 테를 깔고 그 위에 진한 선을
             올려, 어떤 등급 색 위에서도 경계가 끊기지 않게 한다. -->
        <g v-if="selfShape" class="map__self" pointer-events="none">
          <path :d="selfShape.d" class="map__self-halo" />
          <path :d="selfShape.d" class="map__self-line" />
        </g>
        <circle v-for="dt in dots" :key="dt.regionCd" :cx="dt.cx" :cy="dt.cy"
                :r="dt.isSelf ? 4 : 2.6" :fill="dt.color" class="map__dot"
                :class="{ 'is-self': dt.isSelf }"
                @mouseenter="hover = dt.regionCd" />
      </svg>

      <div class="map__side">
        <div v-if="shown && shownName" class="map__info">
          <b>{{ shownName.sigungu }}</b>
          <span class="map__sido">{{ shownName.sido }}</span>
          <span class="map__grade"
                :style="{ '--c': shown.dataStatus === '데이터없음'
                          ? 'var(--g-none)' : GRADE_COLOR[shown.riskGrade ?? ''] }">
            {{ shown.dataStatus === '데이터없음' ? '자료 없음' : shown.riskGrade }}
          </span>
          <span class="map__prob">
            화재가 날 가능성 <b>{{ pct(shown.occurProbability) }}</b>
          </span>
          <span v-if="!hover" class="map__self-note">지금 보는 지역</span>
        </div>

        <ul class="map__legend">
          <li v-for="(n, g) in legend" :key="g" :style="{ '--c': GRADE_COLOR[g as string] }">
            <i /> {{ g }} <b>{{ n }}</b>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.map__wrap { display: flex; gap: var(--sp-md); align-items: stretch; flex-wrap: wrap; }
.map__svg {
  flex: 1 1 220px; min-width: 0; max-width: 300px; height: auto;
  /* 밝은 화면에서는 카드와 같은 흰 바탕. 회색을 깔면 지도가 회색 상자 안에
     갇힌 것처럼 보이고, 시군구 색도 그만큼 죽는다. */
  background: var(--inset);
}
/* 어두운 바탕에서는 살짝 투명해도 색이 살지만, 흰 바탕에서는 그만큼 옅어진다.
   밝은 화면에서는 불투명하게 칠한다. */
/* 경계선은 바탕색으로 긋는다 — 지도 바탕이 흰색이면 흰 선, 어두우면 어두운 선.
   그래야 시군구가 서로 붙지 않으면서 선 자체는 눈에 띄지 않는다. */
.map__area {
  /* 경계선을 바탕색(흰색)으로 긋고 있었다. 밝은 화면에서는 사실상 보이지 않아
     시군구들이 한 덩어리 색면으로 뭉개졌다. 옅은 회색으로 실제 선을 긋는다. */
  stroke: var(--hairline); stroke-width: .45; opacity: 1; cursor: default;
  stroke-linejoin: round;
  transition: opacity .12s ease;
}
:root[data-theme='dark'] .map__area { stroke: var(--inset); }
:root[data-theme='dark'] .map__area { opacity: .8; }
.map__area.is-hover { opacity: 1; }
.map__area.is-self { opacity: 1; }
.map__self-halo { fill: none; stroke: var(--surface-1); stroke-width: 3.6;
                  stroke-linejoin: round; }
.map__self-line { fill: none; stroke: var(--ink); stroke-width: 1.7;
                  stroke-linejoin: round; }
.map__dot { stroke: var(--inset); stroke-width: .5; opacity: 1; }
.map__dot.is-self { stroke: var(--ink); stroke-width: 1.2; opacity: 1; }

.map__side { flex: 1 1 170px; min-width: 0; display: flex; flex-direction: column; }
.map__info {
  display: flex; flex-direction: column; gap: 3px;
  padding: var(--sp-xs) var(--sp-sm); margin-bottom: var(--sp-xs);
  background: var(--inset); border: 1px solid var(--inset-line);
}
.map__info > b { font-size: 15px; }
.map__sido { font-size: 11px; color: var(--ink-subtle); }
/* 등급 색 위의 글자. 색을 박아두면 밝은 화면에서 대비가 무너진다 —
   등급 색을 배경이 아니라 글자와 테두리에 써서 두 테마 모두에서 읽히게 한다. */
.map__grade { align-self: flex-start; padding: 1px 6px;
              background: color-mix(in srgb, var(--c) var(--tint), transparent);
              border-left: 3px solid var(--c);
              color: var(--ink); font-size: 11px; font-weight: 600; }
.map__prob { margin-top: 2px; font-size: 11px; color: var(--ink-subtle); }
.map__prob b { margin-left: 4px; color: var(--ink); font-family: var(--font-mono); }
.map__self-note { font-size: 10px; color: var(--ink-faint); }

.map__legend { list-style: none; margin: auto 0 0; padding: 0; font-size: 11px; color: var(--ink-subtle); }
.map__legend li { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.map__legend i { width: 9px; height: 9px; background: var(--c); flex: 0 0 auto; }
.map__legend b { margin-left: auto; color: var(--ink); font-family: var(--font-mono); }

.hint { margin: 2px 0 var(--sp-sm); font-size: 11px; color: var(--ink-subtle); line-height: 1.6; }
.muted { color: var(--ink-subtle); }
.small { font-size: 11px; }
</style>
