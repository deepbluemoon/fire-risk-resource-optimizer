<script setup lang="ts">
import { onMounted, ref, watchEffect } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import { useAppStore } from './stores/app';
import { NAV } from './router';

const app = useAppStore();
const route = useRoute();
onMounted(() => app.bootstrap());

/** 화면 테마 — 상황실은 어두운 화면이 기본이지만, 밝은 사무실이나 인쇄물 확인에는
 *  밝은 화면이 낫다. 선택은 브라우저에 남겨 다음에 열 때도 유지한다. */
type Theme = 'dark' | 'light';
const THEME_KEY = 'fire.theme';
/** 처음 열면 항상 밝은 화면이다. OS 설정은 따르지 않는다 — 여러 사람이 같은
 *  화면을 보고 이야기하는 자리에서 사람마다 배색이 다르면 설명이 어긋난다.
 *  직접 바꾼 선택만 기억한다. */
const theme = ref<Theme>('light');

onMounted(() => {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') theme.value = saved;
  } catch { /* 저장소를 못 쓰는 환경이면 기본값(밝은 화면)으로 둔다 */ }
});

watchEffect(() => {
  document.documentElement.setAttribute('data-theme', theme.value);
  try { localStorage.setItem(THEME_KEY, theme.value); } catch { /* 무시 */ }
});

const toggleTheme = () => { theme.value = theme.value === 'dark' ? 'light' : 'dark'; };

const isCurrent = (p: string) => p === '/' ? route.path === '/' || route.path.startsWith('/region')
                                           : route.path.startsWith(p);
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <RouterLink to="/" class="brand">
        <!-- 소방모자. 챙이 앞으로 길고 뒤가 늘어진 실루엣이 소방 헬멧의 특징이다. -->
        <svg class="brand__mark" viewBox="0 0 24 24" aria-hidden="true">
          <path class="brand__shell"
                d="M5.4 15.2a6.6 6.6 0 0 1 13.2 0z" />
          <path class="brand__crest" d="M12 8.6v6.6" />
          <path class="brand__brim"
                d="M2.6 15.2h18.8c.5 0 .8.5.6.9-.7 1.3-2.3 2.1-4.2 2.1H6.2c-1.9 0-3.5-.8-4.2-2.1-.2-.4.1-.9.6-.9z" />
          <circle class="brand__badge" cx="12" cy="12" r="1.5" />
        </svg>
        <span class="brand__name">화재위험 예측</span>
        <span class="brand__sub">상황실</span>
      </RouterLink>
      <div class="topbar__spacer" />
      <button class="themebtn" type="button"
              :title="theme === 'dark' ? '밝은 화면으로 바꾸기' : '어두운 화면으로 바꾸기'"
              :aria-label="theme === 'dark' ? '밝은 화면으로 바꾸기' : '어두운 화면으로 바꾸기'"
              @click="toggleTheme">
        <span aria-hidden="true">{{ theme === 'dark' ? '☀' : '☾' }}</span>
        <span class="themebtn__t">{{ theme === 'dark' ? '밝게' : '어둡게' }}</span>
      </button>
      <div class="topbar__meta">
        <!-- 모델 버전·학습 기준은 상단에서 뺐다. 모든 화면에 늘 떠 있을 만큼
             자주 보는 값이 아니고, 운영 상태 화면에 같은 값이 더 자세히 있다. -->
        <span class="domain mono">운영 대시보드</span>
      </div>
    </header>

    <nav class="rail" aria-label="주요 화면">
      <RouterLink v-for="n in NAV" :key="n.path" :to="n.path" class="rail__item"
                  :class="{ 'is-on': isCurrent(n.path) }">
        <span class="rail__title">{{ n.title }}</span>
      </RouterLink>
      <div class="rail__foot">
        <!-- 요구사항 번호(US1·FR-026 등)는 명세 문서의 것이지 쓰는 사람의 것이 아니다.
             화면에 있으면 "내가 봐도 되는 화면인가" 하는 위축만 준다. -->
        <p>의사결정 <b>지원</b> 도구입니다.<br />인력을 자동으로 이동시키지 않습니다.</p>
      </div>
    </nav>

    <main class="main scroll">
      <RouterView v-slot="{ Component }">
        <component :is="Component" class="rise" />
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid; height: 100%;
  /* 두 번째 열을 1fr 로만 두면 그리드 항목의 기본값이 min-width:auto 라
     넓은 표(白space:nowrap)가 열을 밀어내 화면 밖으로 삐져나간다. minmax(0,1fr)
     로 잡아야 안에서 스크롤된다. */
  grid-template-columns: var(--rail) minmax(0, 1fr);
  grid-template-rows: var(--topbar) minmax(0, 1fr);
  grid-template-areas: 'top top' 'rail main';
  overflow: hidden;
}
.topbar {
  grid-area: top; display: flex; align-items: center; gap: var(--sp-md);
  padding: 0 var(--sp-md); background: var(--canvas);
  border-bottom: 1px solid var(--hairline);
}
.themebtn {
  display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 var(--sp-xs);
  background: transparent; color: var(--ink-subtle);
  border: 1px solid var(--hairline); cursor: pointer;
  font-size: 11px; letter-spacing: .32px;
  transition: background .12s, color .12s;
}
.themebtn:hover { background: var(--surface-hover); color: var(--ink); }
@media (max-width: 640px) { .themebtn__t { display: none; } }

.brand { display: flex; align-items: baseline; gap: var(--sp-xs); color: var(--ink); text-decoration: none; }
.brand:hover { text-decoration: none; }
.brand__mark {
  width: 22px; height: 22px; align-self: center; flex: 0 0 auto;
  animation: pulse 2.6s ease-in-out infinite;
}
.brand__shell, .brand__brim { fill: var(--g-critical); }
.brand__crest { stroke: var(--on-primary); stroke-width: 1.4; stroke-linecap: round; }
.brand__badge { fill: var(--on-primary); }
@media (prefers-reduced-motion: reduce) { .brand__mark { animation: none; } }
@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
.brand__name { font-size: 15px; font-weight: 600; letter-spacing: .1px; }
.brand__sub { font-size: 12px; color: var(--ink-subtle); letter-spacing: .32px; }
.topbar__spacer { flex: 1; }
.topbar__meta { display: flex; align-items: center; gap: var(--sp-md); font-size: 11px; color: var(--ink-subtle); }
.stale { color: var(--g-mid-ink); }
.domain { color: var(--ink-faint); }

.rail {
  grid-area: rail; display: flex; flex-direction: column;
  border-right: 1px solid var(--hairline); background: var(--canvas);
}
.rail__item {
  display: flex; align-items: baseline; justify-content: space-between; gap: var(--sp-xs);
  padding: var(--sp-sm) var(--sp-md);
  color: var(--ink-muted); text-decoration: none;
  border-left: 3px solid transparent;
  transition: background .11s, color .11s;
}
.rail__item:hover { background: var(--surface-hover); color: var(--ink); text-decoration: none; }
.rail__item.is-on { background: var(--surface-1); color: var(--ink); border-left-color: var(--primary); }
.rail__title { font-size: 14px; }
.rail__foot {
  margin-top: auto; padding: var(--sp-md);
  border-top: 1px solid var(--hairline); font-size: 11px; color: var(--ink-subtle);
}
.rail__foot p { margin: 0 0 var(--sp-xxs); line-height: 1.5; }
.rail__foot b { color: var(--ink-muted); }
.rail__foot span { font-size: 10px; color: var(--ink-faint); }

.main {
  grid-area: main; padding: var(--sp-lg);
  min-width: 0; min-height: 0; overflow-y: auto; overflow-x: hidden;
}
/* 페이지 안의 어떤 요소도 본문 폭을 넘기지 않는다 */
.main :deep(section) { min-width: 0; }
.main :deep(.grid) { min-width: 0; }
.main :deep(.grid > *) { min-width: 0; }
.main :deep(.tile) { min-width: 0; max-width: 100%; }
/* 넓은 표는 페이지가 아니라 표 자신이 가로 스크롤한다 */
.main :deep(.tblwrap) { max-width: 100%; overflow-x: auto; }

/* --- 좁은 화면 -----------------------------------------------------------
   예전에는 900px 아래에서 rail 을 display:none 으로 감췄다. 그러면 화면을
   옮길 방법이 아예 없어진다 — 태블릿 가로만 되어도 길이 막힌다.
   감추는 대신 세로 목록을 상단 가로 탭으로 바꾼다. */
@media (max-width: 900px) {
  .shell {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: var(--topbar) auto minmax(0, 1fr);
    grid-template-areas: 'top' 'rail' 'main';
  }
  .rail {
    flex-direction: row; overflow-x: auto; overflow-y: hidden;
    border-right: 0; border-bottom: 1px solid var(--hairline);
    scrollbar-width: none;
  }
  .rail::-webkit-scrollbar { display: none; }
  .rail__item {
    flex: 0 0 auto; flex-direction: column; align-items: flex-start; gap: 1px;
    padding: var(--sp-xs) var(--sp-sm);
    border-left: 0; border-bottom: 2px solid transparent;
  }
  .rail__item.is-on { border-left: 0; border-bottom-color: var(--primary); background: transparent; }
  /* 안내 문구는 탭 줄에 들어갈 자리가 없다 — 좁은 화면에서는 접는다 */
  .rail__foot { display: none; }
  .main { padding: var(--sp-md); }
}

/* --- 손에 들고 보는 화면 -------------------------------------------------- */
@media (max-width: 640px) {
  .topbar { padding: 0 var(--sp-sm); gap: var(--sp-xs); }
  .brand__sub { display: none; }
  .main { padding: var(--sp-sm); }
  /* 페이지 머리말의 제목과 조작부를 세로로 쌓는다 */
  .main :deep(.head) { flex-direction: column; align-items: stretch; gap: var(--sp-sm); }
  .main :deep(.head__ctl) { flex-wrap: wrap; }
  .main :deep(h1) { font-size: 22px; }
}
</style>
