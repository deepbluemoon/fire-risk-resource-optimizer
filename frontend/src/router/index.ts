import { createRouter, createWebHistory } from 'vue-router';

export const NAV = [
  { path: '/',             name: 'dashboard',    title: '오늘 위험도' },
  { path: '/allocation',   name: 'allocation',   title: '인력 배분' },
  { path: '/verification', name: 'verification', title: '지난 예측 확인' },
  { path: '/ops',          name: 'ops',          title: '계산·모델 상태' },
];

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/',                    name: 'dashboard',    component: () => import('../pages/Dashboard.vue') },
    { path: '/region/:regionCd/:date', name: 'region',    component: () => import('../pages/RegionDetail.vue'), props: true },
    { path: '/allocation',          name: 'allocation',   component: () => import('../pages/Allocation.vue') },
    { path: '/verification',        name: 'verification', component: () => import('../pages/Verification.vue') },
    { path: '/ops',                 name: 'ops',          component: () => import('../pages/Ops.vue') },
    { path: '/:pathMatch(.*)*',     redirect: '/' },
  ],
});
