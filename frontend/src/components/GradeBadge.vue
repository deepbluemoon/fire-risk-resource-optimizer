<script setup lang="ts">
import { computed } from 'vue';
import { GRADE_COLOR, GRADE_INK } from '../services/api';

const p = defineProps<{ grade: string | null; status?: string | null; compact?: boolean }>();

/** 색만으로 정보를 전달하지 않는다 — 채움 정도를 함께 쓴다 (접근성).
 *
 *  예전에는 ○ ◔ ◑ ● 유니코드 문자를 썼는데, 글자마다 실제 크기와 굵기가 달라
 *  등급을 세로로 늘어놓으면 동그라미 크기가 제각각으로 보였다.
 *  같은 반지름의 원 하나에 채움만 달리하면 크기가 어긋날 수 없다. */
const FILL: Record<string, number> = { '낮음': 0, '보통': 0.34, '높음': 0.67, '매우높음': 1 };
const fill = computed(() => FILL[p.grade ?? ''] ?? 0);

/** 원의 일부를 채운다. 0 이면 테두리만, 1 이면 꽉 채운다.
 *  아래에서 위로 차오르게 해 '차오를수록 위험' 이라는 방향을 만든다. */
const R = 5;
const clipY = computed(() => R - fill.value * (R * 2));
</script>

<template>
  <span v-if="p.status === '데이터없음'" class="badge badge--none">
    <svg class="badge__ico" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M3.5 3.5l5 5M8.5 3.5l-5 5" />
    </svg>
    자료 없음
  </span>

  <span v-else-if="p.grade" class="badge"
        :style="{ '--c': GRADE_COLOR[p.grade], '--ci': GRADE_INK[p.grade] }">
    <svg class="badge__ico" viewBox="0 0 12 12" aria-hidden="true">
      <defs>
        <clipPath :id="`gb-${p.grade}`">
          <rect x="0" :y="6 + clipY" width="12" :height="12" />
        </clipPath>
      </defs>
      <circle cx="6" cy="6" :r="R" class="badge__ring" />
      <circle v-if="fill > 0" cx="6" cy="6" :r="R" class="badge__fill"
              :clip-path="`url(#gb-${p.grade})`" />
    </svg>
    <template v-if="!p.compact">{{ p.grade }}</template>
  </span>

  <span v-else class="badge badge--none">—</span>
</template>

<style scoped>
.badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 1px 8px 2px; border-radius: 0;
  font-size: 12px; font-weight: 500; letter-spacing: .32px; white-space: nowrap;
  /* 글자는 --ci(읽히는 색), 표식·테두리는 --c(선명한 색).
     한 색으로 둘 다 하면 읽히긴 해도 탁해진다. */
  color: var(--ci, var(--ink-muted));
  border-left: 3px solid var(--c, var(--ink-faint));
  background: color-mix(in srgb, var(--c, #6f6f6f) var(--tint), transparent);
}
/* 모든 등급이 같은 상자를 쓴다 — 글자로 그릴 때 생기던 크기 차이가 없다 */
.badge__ico { width: 12px; height: 12px; flex: 0 0 auto; display: block; }
.badge__ring { fill: none; stroke: var(--c, var(--ink-faint)); stroke-width: 1.4; }
.badge__fill { fill: var(--c, var(--ink-faint)); }
.badge--none .badge__ico { stroke: var(--g-none); stroke-width: 1.4; fill: none;
                           stroke-linecap: round; }
.badge--none { --c: var(--g-none); --ci: var(--g-none-ink); font-style: normal; }
</style>
