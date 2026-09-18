<script setup lang="ts">
/** 내 위치의 지금 날씨 — 화면 맨 위에서 오늘 조건을 바로 보여준다.
 *
 *  왜 필요한가
 *    위험 등급만 보면 '왜 오늘 높은지' 가 안 보인다. 건조하고 바람 부는 날이라는
 *    사실을 같은 화면에서 보면 등급이 납득된다.
 *
 *  설계
 *    - 브라우저 위치는 사용자가 거부할 수 있고 http 에서는 아예 막힌다.
 *      실패하면 조용히 숨기지 않고 '위치를 못 받았다' 고 말한 뒤 전국 기준으로 둔다.
 *    - 좌표를 받으면 우리 251개 시군구 중 가장 가까운 곳을 찾아 그 지역의
 *      위험 등급까지 함께 보여준다. 날씨와 위험도가 한 줄에서 이어진다.
 *    - 날씨 아이콘은 인라인 SVG 다. 아이콘 폰트나 외부 이미지를 부르면
 *      폐쇄망에서 깨진다.
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api, GRADE_COLOR } from '../services/api';

/** 좌표를 주면 그곳 날씨를 보여준다(지역 상세). 안 주면 내 위치를 쓴다(메인).
 *  지역 상세에서는 이미 그 지역을 보고 있으므로 '가까운 지역으로 이동' 버튼을 뺀다. */
const p = defineProps<{
  date: string;
  lat?: number | null;
  lon?: number | null;
  placeName?: string;
}>();
const router = useRouter();
const fixed = computed(() => p.lat != null && p.lon != null);

const wx = ref<any | null>(null);
const near = ref<any | null>(null);
const state = ref<'loading' | 'ok' | 'nogeo' | 'failed'>('loading');
const detail = ref('');

/** WMO 날씨 코드 → 한 마디 + 아이콘 종류.
 *  https://open-meteo.com 의 weather_code 정의를 그대로 옮겼다. */
const WMO: Record<number, { t: string; i: string }> = {
  0: { t: '맑음', i: 'sun' },
  1: { t: '대체로 맑음', i: 'sun' },
  2: { t: '구름 조금', i: 'cloudsun' },
  3: { t: '흐림', i: 'cloud' },
  45: { t: '안개', i: 'fog' }, 48: { t: '짙은 안개', i: 'fog' },
  51: { t: '이슬비', i: 'rain' }, 53: { t: '이슬비', i: 'rain' }, 55: { t: '이슬비', i: 'rain' },
  56: { t: '어는 이슬비', i: 'rain' }, 57: { t: '어는 이슬비', i: 'rain' },
  61: { t: '약한 비', i: 'rain' }, 63: { t: '비', i: 'rain' }, 65: { t: '강한 비', i: 'rain' },
  66: { t: '어는 비', i: 'rain' }, 67: { t: '어는 비', i: 'rain' },
  71: { t: '약한 눈', i: 'snow' }, 73: { t: '눈', i: 'snow' }, 75: { t: '많은 눈', i: 'snow' },
  77: { t: '싸락눈', i: 'snow' },
  80: { t: '소나기', i: 'rain' }, 81: { t: '소나기', i: 'rain' }, 82: { t: '강한 소나기', i: 'rain' },
  85: { t: '눈 소나기', i: 'snow' }, 86: { t: '눈 소나기', i: 'snow' },
  95: { t: '천둥번개', i: 'storm' }, 96: { t: '천둥번개', i: 'storm' }, 99: { t: '우박·천둥', i: 'storm' },
};

const sky = computed(() => WMO[Number(wx.value?.weather_code)] ?? { t: '—', i: 'cloud' });

/** 화재 관점의 한 줄. 온습도만 나열하면 그래서 어떻다는 말이 없다. */
const note = computed(() => {
  const w = wx.value;
  if (!w) return '';
  const rh = Number(w.relative_humidity_2m);
  const ws = Number(w.wind_speed_10m);
  const rain = Number(w.precipitation ?? 0);
  if (rain > 0) return '비가 내려 화재 위험이 낮아집니다.';
  if (rh < 30 && ws >= 4) return '건조하고 바람이 강해 불이 번지기 쉽습니다.';
  if (rh < 30) return '공기가 매우 건조합니다.';
  if (ws >= 6) return '바람이 강합니다. 불씨가 번지기 쉽습니다.';
  if (rh > 70) return '습도가 높아 화재 위험이 낮은 편입니다.';
  return '평소 수준의 조건입니다.';
});

function haversine(a: number, b: number, c: number, d: number) {
  const R = 6371, r = Math.PI / 180;
  const dLat = (c - a) * r, dLon = (d - b) * r;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}

async function fetchWeather(lat: number, lon: number) {
  // models 파라미터는 넣지 않는다 — kma_seamless 는 current 블록을 비워서 보낸다.
  const q = new URLSearchParams({
    latitude: String(lat), longitude: String(lon),
    current: 'temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,weather_code,is_day',
    timezone: 'Asia/Seoul', wind_speed_unit: 'ms',
  });
  const r = await fetch(`https://api.open-meteo.com/v1/forecast?${q}`);
  if (!r.ok) throw new Error(`날씨 서버 응답 ${r.status}`);
  return (await r.json()).current;
}

/** 251개 시군구 중 가장 가까운 곳 — 좌표는 이미 서비스가 들고 있다 */
async function nearestRegion(lat: number, lon: number) {
  try {
    const pts = await api.mapPoints(p.date);
    let best: any = null, bd = Infinity;
    for (const x of pts) {
      if (x.lat == null || x.lon == null) continue;
      const d = haversine(lat, lon, Number(x.lat), Number(x.lon));
      if (d < bd) { bd = d; best = { ...x, distKm: d }; }
    }
    return best;
  } catch { return null; }
}

function locate(): Promise<GeolocationPosition> {
  return new Promise((res, rej) => {
    if (!navigator.geolocation) return rej(new Error('이 브라우저는 위치를 지원하지 않습니다'));
    navigator.geolocation.getCurrentPosition(res, rej, { timeout: 8000, maximumAge: 600000 });
  });
}

onMounted(async () => {
  // 좌표를 받은 경우(지역 상세)는 위치 권한을 물을 이유가 없다.
  if (fixed.value) {
    try {
      wx.value = await fetchWeather(Number(p.lat), Number(p.lon));
      state.value = 'ok';
    } catch { state.value = 'failed'; }
    return;
  }

  let lat: number, lon: number, located = false;
  try {
    const pos = await locate();
    lat = pos.coords.latitude; lon = pos.coords.longitude; located = true;
  } catch (e: any) {
    lat = 37.5665; lon = 126.9780;   // 위치를 못 받으면 서울 기준으로 둔다
    detail.value = e?.code === 1 ? '위치 사용을 허용하지 않아' : '위치를 확인할 수 없어';
  }
  try {
    const [w, n] = await Promise.all([fetchWeather(lat, lon), nearestRegion(lat, lon)]);
    wx.value = w; near.value = n;
    state.value = located ? 'ok' : 'nogeo';
  } catch {
    state.value = 'failed';
  }
});

function openRegion() {
  if (near.value) router.push(`/region/${encodeURIComponent(near.value.regionCd)}/${p.date}`);
}
</script>

<template>
  <div v-if="state !== 'failed'" class="wx" :class="{ 'is-loading': state === 'loading' }">
    <template v-if="state === 'loading'">
      <span class="wx__muted">지금 날씨를 불러오는 중…</span>
    </template>

    <template v-else>
      <!-- 아이콘 -->
      <svg class="wx__ico" viewBox="0 0 24 24" aria-hidden="true">
        <g v-if="sky.i === 'sun'">
          <circle cx="12" cy="12" r="5" />
          <g class="wx__ray">
            <path d="M12 1v3M12 20v3M1 12h3M20 12h3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M19.8 4.2l-2.1 2.1M6.3 17.7l-2.1 2.1" />
          </g>
        </g>
        <g v-else-if="sky.i === 'cloudsun'">
          <circle cx="8.5" cy="8.5" r="3.6" />
          <path class="wx__f" d="M8 19h9a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.6 1.2A3 3 0 0 0 8 19z" />
        </g>
        <g v-else-if="sky.i === 'fog'">
          <path class="wx__f" d="M7 14h10a3.4 3.4 0 0 0 .3-6.8 4.9 4.9 0 0 0-9.5 1.2A2.9 2.9 0 0 0 7 14z" />
          <path d="M4 17.5h16M6 20.5h12" />
        </g>
        <g v-else-if="sky.i === 'rain'">
          <path class="wx__f" d="M7 15h10a3.4 3.4 0 0 0 .3-6.8 4.9 4.9 0 0 0-9.5 1.2A2.9 2.9 0 0 0 7 15z" />
          <path class="wx__drop" d="M9 18l-1 3M13 18l-1 3M17 18l-1 3" />
        </g>
        <g v-else-if="sky.i === 'snow'">
          <path class="wx__f" d="M7 15h10a3.4 3.4 0 0 0 .3-6.8 4.9 4.9 0 0 0-9.5 1.2A2.9 2.9 0 0 0 7 15z" />
          <path d="M9 19h.01M13 19h.01M17 19h.01M11 21.5h.01M15 21.5h.01" stroke-linecap="round" stroke-width="2.2" />
        </g>
        <g v-else-if="sky.i === 'storm'">
          <path class="wx__f" d="M7 14h10a3.4 3.4 0 0 0 .3-6.8 4.9 4.9 0 0 0-9.5 1.2A2.9 2.9 0 0 0 7 14z" />
          <path d="M13 16l-3 4h3l-1.5 3.5" />
        </g>
        <g v-else>
          <path class="wx__f" d="M7 16h10a3.6 3.6 0 0 0 .3-7.2 5.2 5.2 0 0 0-10 1.3A3.1 3.1 0 0 0 7 16z" />
        </g>
      </svg>

      <!-- 어디 날씨인지 -->
      <span v-if="placeName" class="wx__place">{{ placeName }}</span>

      <!-- 기온·상태 -->
      <span class="wx__temp">{{ Number(wx?.temperature_2m).toFixed(0) }}<i>℃</i></span>
      <span class="wx__sky">{{ sky.t }}</span>

      <!-- 세부 -->
      <span class="wx__kv">습도 <b>{{ Number(wx?.relative_humidity_2m).toFixed(0) }}%</b></span>
      <span class="wx__kv">바람 <b>{{ Number(wx?.wind_speed_10m).toFixed(1) }}m/s</b></span>
      <span v-if="Number(wx?.precipitation) > 0" class="wx__kv">
        비 <b>{{ Number(wx?.precipitation).toFixed(1) }}mm</b>
      </span>

      <span class="wx__note">{{ note }}</span>

      <!-- 가장 가까운 시군구. 지역 상세에서는 이미 그 지역을 보고 있으므로 뺀다. -->
      <button v-if="near && !fixed" class="wx__near" type="button" @click="openRegion"
              :title="`${near.sido} ${near.sigungu} 상세 보기`">
        <span class="wx__dot" :style="{ '--c': GRADE_COLOR[near.riskGrade ?? ''] ?? 'var(--g-none)' }" />
        {{ near.sigungu }}
        <b>{{ near.riskGrade ?? '자료 없음' }}</b>
        <span class="wx__arrow" aria-hidden="true">›</span>
      </button>

      <span v-if="state === 'nogeo' && !fixed" class="wx__muted wx__why">
        {{ detail }} 서울 기준으로 보여드립니다
      </span>
    </template>
  </div>
</template>

<style scoped>
.wx {
  display: flex; align-items: center; gap: var(--sp-sm); flex-wrap: wrap;
  padding: var(--sp-xs) var(--sp-md); margin-bottom: var(--sp-md);
  background: var(--surface-1); border: 1px solid var(--hairline);
  font-size: 12px; color: var(--ink-muted);
}
.wx.is-loading { color: var(--ink-faint); }

.wx__ico { width: 26px; height: 26px; flex: 0 0 auto;
           fill: none; stroke: var(--ink-muted); stroke-width: 1.6;
           stroke-linecap: round; stroke-linejoin: round; }
.wx__ico .wx__f { fill: color-mix(in srgb, var(--ink-muted) var(--tint), transparent); }
.wx__ray { animation: spin 24s linear infinite; transform-origin: 12px 12px; }
.wx__drop { animation: drip 1.6s ease-in-out infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes drip { 0%,100% { opacity: .35; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .wx__ray, .wx__drop { animation: none; }
}

.wx__place {
  padding-right: var(--sp-sm); border-right: 1px solid var(--hairline);
  color: var(--ink); font-weight: 600;
}
.wx__temp { font-size: 20px; font-weight: 300; color: var(--ink); font-family: var(--font-mono); }
.wx__temp i { font-size: 12px; font-style: normal; margin-left: 1px; }
.wx__sky { color: var(--ink); }
.wx__kv { color: var(--ink-subtle); }
.wx__kv b { margin-left: 3px; color: var(--ink-muted); font-family: var(--font-mono); font-weight: 400; }

/* 해석 문장을 오른쪽으로 민다. 이걸 버튼에 걸어두면 버튼이 없는 지역 상세에서는
   아무것도 안 밀려 내용이 왼쪽에 몰리고, 메인과 생김새가 달라진다.
   문장에 걸면 두 화면 모두 같은 모양으로 끝난다. */
.wx__note { margin-left: auto; color: var(--ink-subtle); }
/* 좁아져 줄이 바뀌면 오른쪽으로 미는 것이 오히려 어색하다 — 왼쪽 정렬로 되돌린다 */
@media (max-width: 900px) {
  .wx__note { margin-left: 0; flex-basis: 100%; }
}
@media (max-width: 640px) {
  /* 손에 들고 볼 때는 한 줄에 다 못 넣는다 — 기온·상태만 남기고 접는다 */
  .wx { gap: var(--sp-xs); padding: var(--sp-xs) var(--sp-sm); }
  .wx__place { flex-basis: 100%; padding-right: 0; border-right: 0; }
  .wx__near { flex-basis: 100%; justify-content: flex-start; }
}

.wx__near {
  display: inline-flex; align-items: center; gap: 6px;
  height: 26px; padding: 0 var(--sp-xs);
  background: transparent; color: var(--ink-muted);
  border: 1px solid var(--hairline); cursor: pointer; font-size: 11px;
  transition: background .12s, color .12s;
}
.wx__near:hover { background: var(--surface-hover); color: var(--ink); }
.wx__near b { font-weight: 600; color: var(--ink); }
.wx__dot { width: 8px; height: 8px; background: var(--c); flex: 0 0 auto; }
.wx__arrow { color: var(--ink-faint); }

.wx__muted { color: var(--ink-faint); }
.wx__why { flex-basis: 100%; font-size: 11px; }
</style>
