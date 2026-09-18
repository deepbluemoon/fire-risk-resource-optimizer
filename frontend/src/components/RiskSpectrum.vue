<script setup lang="ts">
/** 251개 시군구를 한 줄의 막대로. 관할 전체 상태가 한눈에 들어온다.
 *
 *  막대 높이는 **선택한 정렬 기준의 실제 값**에 비례한다. 예전에는 순서에 따라
 *  높이를 고정(상위 25개는 100%, 그 다음은 80%…)했는데, 값과 무관한 높이는
 *  "여기가 저기보다 5배 위험하다" 처럼 없는 차이를 보여준다.
 */
import { computed, ref } from 'vue';
import { GRADE_COLOR, pct, num, type RiskItem } from '../services/api';

const p = defineProps<{
  items: RiskItem[];
  selected?: string | null;
  /** 막대 높이·툴팁에 쓸 기준. Dashboard 의 정렬 기준과 같은 값을 받는다. */
  metric?: string;
  /** 색을 살려 둘 지역들. null 이면 전부 색.
   *
   *  시도·최소 등급으로 고른 결과를 걸러내지 않고 '나머지를 회색으로 누르는' 방식으로
   *  보여주려는 것이다. 걸러내면 고른 것이 전국 어디쯤인지가 사라진다. */
  active?: Set<string> | null;
}>();
const emit = defineEmits<{ pick: [RiskItem] }>();

const METRIC: Record<string, { label: string; get: (i: RiskItem) => number | null; fmt: (v: number) => string }> = {
  count: { label: '예상 화재 건수', get: (i) => i.expectedCount ?? null, fmt: (v) => `${num(v)}건` },
  probability: { label: '화재가 날 가능성', get: (i) => i.occurProbability ?? null, fmt: (v) => pct(v) },
  damage: { label: '대응 부담', get: (i) => i.expectedDamageLoad ?? null, fmt: (v) => num(v) },
  spread: { label: '불이 번질 가능성', get: (i) => i.spreadProbability ?? null, fmt: (v) => pct(v) },
  longburn: { label: '진화가 길어질 가능성', get: (i) => i.longburnProbability ?? null, fmt: (v) => pct(v) },
};

const spec = computed(() => METRIC[p.metric ?? 'count'] ?? METRIC.count);

/** 고른 범위 밖이면 true — 색을 빼고 뒤로 물린다. */
const isDim = (it: RiskItem) => !!p.active && !p.active.has(it.regionCd);

/** SVG 좌표계 — 막대 1칸을 3, 사이 간격을 1 로 잡는다. 화면 폭에 맞춰 늘어난다. */
const BW = 3;
const GAP = 1;
const VH = 100;
const VW = computed(() => Math.max(1, p.items.length) * (BW + GAP));

/** 화면에 보이는 순서 그대로 쓴다 — 목록의 정렬 기준과 어긋나면 안 된다. */
const bars = computed(() => {
  const vals = p.items.map((i) => spec.value.get(i)).filter((v): v is number => v != null);
  const max = vals.length ? Math.max(...vals) : 1;
  return p.items.map((it, idx) => {
    const v = spec.value.get(it);
    // 최솟값도 보이도록 바닥을 12 로 둔다. 0 이면 아예 안 보여 클릭도 안 된다.
    const h = v == null || max <= 0 ? 12 : 12 + (v / max) * 88;
    return { it, idx, value: v, h, x: idx * (BW + GAP) };
  });
});

const counts = computed(() => {
  const c: Record<string, number> = { '매우높음': 0, '높음': 0, '보통': 0, '낮음': 0, '데이터없음': 0 };
  // 눌러 둔 지역은 세지 않는다 — 색은 경기도만인데 숫자가 전국이면 어긋난다.
  for (const i of p.items) {
    if (isDim(i)) continue;
    if (i.dataStatus === '데이터없음') c['데이터없음']++;
    else if (i.riskGrade) c[i.riskGrade]++;
  }
  return c;
});

/** 팝업 — 어느 막대가 어느 지역인지 바로 보이게 한다. 브라우저 기본 title 은
 *  1초 가까이 기다려야 뜨고 서식을 줄 수 없다. */
const hover = ref<{ idx: number; leftPct: number } | null>(null);
const hovered = computed(() => (hover.value == null ? null : bars.value[hover.value.idx]));

function onEnter(idx: number) {
  hover.value = { idx, leftPct: ((idx + 0.5) / Math.max(bars.value.length, 1)) * 100 };
}
</script>

<template>
  <div class="spec">
    <div class="spec__wrap">
      <!-- 251개를 flex 로 그리면 1px 미만 반올림이 막대마다 달라 아래 끝이 들쭉날쭉해
           보인다(값은 정확히 내림차순인데도). SVG 로 좌표를 직접 잡으면 그 문제가 없다. -->
      <!-- 막대는 regionCd 로 묶여 있어 정렬을 바꿔도 요소가 새로 만들어지지 않는다.
           그래서 rise 애니메이션이 처음 한 번만 돌고, 순위가 거의 그대로인 기준끼리
           오갈 때는 화면이 아예 멈춘 것처럼 보였다. 기준을 key 에 넣어 기준이 바뀔
           때마다 막대를 새로 그리게 한다 — 순위가 안 바뀌어도 눈에 보이게 다시 자란다. -->
      <svg class="spec__bar" :key="spec.label" :viewBox="`0 0 ${VW} ${VH}`" preserveAspectRatio="none"
           role="img" :aria-label="`전체 ${items.length}개 시군구 ${spec.label} 분포`"
           @mouseleave="hover = null">
        <rect v-for="b in bars" :key="b.it.regionCd" class="spec__col"
              :class="{ 'is-sel': selected === b.it.regionCd, 'is-hover': hover?.idx === b.idx,
                        'is-dim': isDim(b.it) }"
              :x="b.x" :y="VH - b.h" :width="BW" :height="b.h"
              :style="{
                '--c': isDim(b.it) || b.it.dataStatus === '데이터없음'
                  ? 'var(--g-none)' : GRADE_COLOR[b.it.riskGrade ?? ''],
                '--d': `${Math.min(b.idx, 120) * 6}ms`,
              }"
              :aria-label="`${b.it.sido} ${b.it.sigungu} · ${b.it.riskGrade ?? '데이터 없음'}`"
              @mouseenter="onEnter(b.idx)"
              @click="emit('pick', b.it)" />
      </svg>

      <transition name="pop">
        <div v-if="hovered" class="spec__pop"
             :style="{ left: `${hover!.leftPct}%` }"
             :class="{ 'is-left': hover!.leftPct < 18, 'is-right': hover!.leftPct > 82 }">
          <span class="spec__pop-rank">{{ hovered.idx + 1 }}위</span>
          <span class="spec__pop-where">
            <b>{{ hovered.it.sigungu }}</b>
            <span class="spec__pop-sido">{{ hovered.it.sido }}</span>
          </span>
          <span class="spec__pop-val">
            {{ spec.label }}
            <b>{{ hovered.value == null ? '자료 없음' : spec.fmt(hovered.value) }}</b>
          </span>
        </div>
      </transition>
    </div>

    <div class="spec__legend">
      <span v-for="(n, g) in counts" :key="g" class="spec__key"
            :style="{ '--c': g === '데이터없음' ? 'var(--g-none)' : GRADE_COLOR[g as string] }">
        <i /> {{ g }} <b class="num">{{ n }}</b>
      </span>
      <span class="spec__hint">막대에 마우스를 올리면 지역이 표시됩니다 · 왼쪽일수록 위험</span>
    </div>
  </div>
</template>

<style scoped>
.spec__wrap { position: relative; }
/* 밝은 화면에서는 거의 불투명하게, 어두운 화면에서는 예전처럼 살짝 투명하게 */
.spec { --bar-o: .95; }
:root[data-theme='dark'] .spec { --bar-o: .74; }
.spec__bar { display: block; width: 100%; height: 56px; }

.spec__col {
  fill: var(--c); opacity: var(--bar-o); cursor: pointer;
  transition: opacity .14s ease;
  /* transform-box: fill-box 를 줘야 transform-origin 이 막대 **자기** 상자를
     기준으로 잡힌다. 없으면 SVG 전체 기준이라 막대가 엉뚱한 데서 자란다. */
  transform-box: fill-box; transform-origin: bottom;
  animation: rise .55s cubic-bezier(.16,.84,.44,1) both;
  animation-delay: var(--d);
}
.spec__col.is-dim { opacity: .28; }
.spec__col:hover, .spec__col.is-hover { opacity: 1; }
.spec__col.is-sel { opacity: 1; stroke: var(--ink); stroke-width: 1; }

/* 아래에서 자라 오르는 효과. SVG 라 좌표가 정확해서 flex 때처럼 아래 끝이
   흔들리지 않는다. */
@keyframes rise {
  from { opacity: 0; transform: scaleY(.03); }
  to   { opacity: var(--bar-o); transform: scaleY(1); }
}
@media (prefers-reduced-motion: reduce) {
  .spec__col { animation: none; opacity: var(--bar-o); }
}

/* 팝업 — 어느 막대가 어느 지역인지 */
.spec__pop {
  position: absolute; bottom: calc(100% + 7px); transform: translateX(-50%);
  display: flex; align-items: baseline; gap: 7px; white-space: nowrap;
  padding: 6px 10px; background: var(--ink); color: var(--surface);
  font-size: 11px; line-height: 1.3; pointer-events: none; z-index: 5;
}
.spec__pop.is-left  { transform: translateX(-12%); }
.spec__pop.is-right { transform: translateX(-88%); }
.spec__pop-rank { opacity: .55; font-variant-numeric: tabular-nums; }
.spec__pop b { font-size: 12px; font-weight: 600; }
.spec__pop-where { display: inline-flex; align-items: baseline; gap: 5px; }
.spec__pop-sido { opacity: .6; font-size: 10px; }
.spec__pop-val { padding-left: 8px; margin-left: 2px;
                 border-left: 1px solid color-mix(in srgb, currentColor 30%, transparent);
                 opacity: .7; }
.spec__pop-val b { margin-left: 4px; opacity: 1; font-variant-numeric: tabular-nums; }

.pop-enter-active, .pop-leave-active { transition: opacity .12s ease, translate .12s ease; }
.pop-enter-from, .pop-leave-to { opacity: 0; translate: 0 3px; }

.spec__legend {
  display: flex; align-items: center; gap: var(--sp-md); flex-wrap: wrap;
  margin-top: var(--sp-xs); font-size: 12px; color: var(--ink-subtle);
}
.spec__key { display: inline-flex; align-items: center; gap: 5px; }
.spec__key i { width: 9px; height: 9px; background: var(--c); display: inline-block; }
.spec__key b { color: var(--ink); font-weight: 600; }
.spec__hint { margin-left: auto; font-size: 11px; color: var(--ink-faint); }
</style>
