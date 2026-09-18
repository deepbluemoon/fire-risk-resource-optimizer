<script setup lang="ts">
/** SC-004 — 데이터가 최신이 아님을 100% 인지할 수 있어야 한다.
 *  오래된 데이터를 당일 데이터인 것처럼 표시하지 않는다. */
import { computed } from 'vue';
import { dateTimeKo, type Freshness } from '../services/api';

const p = defineProps<{ freshness: Freshness | null; targetDate?: string }>();

const tone = computed(() => {
  if (!p.freshness) return null;
  if (p.freshness.status === '최신') return null;
  return p.freshness.status === '폴백' ? 'warn' : 'info';
});
const msg = computed(() => {
  const f = p.freshness;
  if (!f) return '';
  if (f.status === '폴백')
    return '오늘 날씨 정보를 받지 못해, 이 지역의 평소 수준으로만 계산했습니다. '
         + '날씨에 따른 변화는 반영되지 않았습니다.';
  return '지금 보시는 예측은 가장 최근 것이 아닙니다. 아래 계산 시각을 확인해 주세요.';
});
const at = computed(() =>
  p.freshness?.predictedAt ? dateTimeKo(p.freshness.predictedAt) : '기록 없음');
</script>

<template>
  <div v-if="tone" class="fb" :class="`fb--${tone}`" role="status">
    <span class="fb__tag">{{ p.freshness?.status === '폴백' ? '날씨 미반영' : '최신 아님' }}</span>
    <span class="fb__msg">{{ msg }}</span>
    <span class="fb__at">계산 시각 {{ at }}</span>
  </div>
</template>

<style scoped>
.fb {
  display: flex; align-items: center; gap: var(--sp-sm);
  /* 아래에 날씨 위젯이 바로 붙는다 — 여백이 없으면 두 줄이 한 덩어리로 보인다 */
  padding: var(--sp-xs) var(--sp-md); margin-bottom: var(--sp-md);
  border-left: 3px solid var(--c);
  background: color-mix(in srgb, var(--c) var(--tint-soft), var(--surface-1));
  font-size: 12px; color: var(--ink-muted);
}
.fb--warn { --c: var(--g-high); }
.fb--info { --c: var(--primary); }
.fb__tag { color: var(--c); font-weight: 600; letter-spacing: .32px; }
.fb__msg { flex: 1; color: var(--ink); }
.fb__at { font-family: var(--font-mono); color: var(--ink-subtle); }
</style>
