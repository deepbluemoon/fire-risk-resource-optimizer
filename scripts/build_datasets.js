/**
 * 화재위험 예측 - 모델별 학습 데이터셋 생성
 *
 * 산출:
 *   data/datasets/occurrence_*.csv  ① 발생   (시군구x일 패널, Poisson + offset)
 *   data/datasets/spread_*.csv      ② 확산   (건 단위, Tweedie + 연소확대 이진)
 *   data/datasets/cause_*.csv       ③ 원인   (건 단위, 10-class)
 *   data/datasets/station_map.csv   기상 지점->시군구 배정표 (근거 기록용)
 *
 * 분할: 시계열 전진 분할 (모든 모델 동일 경계)
 *   train 2021-01-01 ~ 2022-06-30 / val 2022-07-01 ~ 2022-12-31 / test 2023 전체
 *
 * 실행: NODE_PATH=<mysql2 경로> node scripts/build_datasets.js
 */
const fs = require('fs');
const path = require('path');
const mysql = require('mysql2/promise');

// 자격증명은 저장소에 커밋하지 않는다. .env 또는 FIRE_DB_* 환경변수에서 읽는다 (.env.example 참조)
try {
  for (const line of fs.readFileSync(path.join(__dirname, '..', '.env'), 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
} catch (e) { /* .env 없으면 환경변수만 사용 */ }

const DB = { host: process.env.FIRE_DB_HOST, port: Number(process.env.FIRE_DB_PORT || 13306),
             user: process.env.FIRE_DB_USER, password: process.env.FIRE_DB_PASS,
             database: process.env.FIRE_DB_NAME, connectTimeout: 30000 };
if (!DB.host || !DB.password) {
  console.error('DB 접속정보가 없습니다. .env 를 만들거나 FIRE_DB_* 환경변수를 설정하세요 (.env.example 참조).');
  process.exit(1);
}
const OUT = path.join(__dirname, '..', 'data', 'datasets');

const SPLIT = {
  train: ['2021-01-01', '2022-06-30'],
  val:   ['2022-07-01', '2022-12-31'],
  test:  ['2023-01-01', '2023-12-31'],
};
const splitOf = d => d <= SPLIT.train[1] ? 'train' : d <= SPLIT.val[1] ? 'val' : 'test';

// 시도명 정규화: 개칭 이전/이후를 하나로 (강원 2023-06, 전북 2024-01)
const SIDO = s => String(s||'').trim()
  .replace('강원특별자치도','강원도').replace('전북특별자치도','전라북도');
const KEY  = (sido, sgg) => SIDO(sido).replace(/\s+/g,'') + '|' + String(sgg||'').trim().replace(/\s+/g,'');
// 'OO시XX구' -> 'OO시'  (보조 테이블이 시 단위로만 있을 때의 롤업)
const ROLLUP = g => { const s = String(g||'').trim().replace(/\s+/g,'');
  const m = s.match(/^(.+?시)[가-힣]+구$/); return m ? m[1] : s; };
// 보조데이터(산림)와 화재 원자료의 행정구역 표기 차이 보정
//   세종: 화재 '세종특별자치시|세종'      vs 산림 '세종특별자치시|세종시'
//   군위: 화재 '대구광역시|군위군'(2023 편입) vs 산림 '경상북도|군위군'
const PROFILE_ALIAS = {
  '세종특별자치시|세종': '세종특별자치시|세종시',
  '대구광역시|군위군':   '경상북도|군위군',
};

/* ---------------------------------------------------------------- 기상 지점 배정
 * 좌표 메타가 없으므로 지점명<->시군구명 대응 + 행정 규칙으로 배정한다.
 * 우선순위: 관내 지점 직접 배정 > 시도 평균 폴백.
 * 동일 시군구에 복수 지점이면 pref 가 낮은 쪽(주 관측소)을 쓴다.
 */
// 지점명이 시군구명과 다르거나 여러 시군구를 대표하는 경우의 명시 규칙
const STATION_RULES = {
  108: { sido:'서울특별시', all:true },                    // 서울 25개 구 공용
  112: { sido:'인천광역시', all:true, except:['강화군','옹진군'] },
  133: { sido:'대전광역시', all:true },
  143: { sido:'대구광역시', all:true, except:['군위군'] },
  152: { sido:'울산광역시', all:true },
  156: { sido:'광주광역시', all:true },
  159: { sido:'부산광역시', all:true },
  119: { sido:'경기도',   prefix:'수원시' },               // 수원시 4개 구
  131: { sido:'충청북도', prefix:'청주시' },
  138: { sido:'경상북도', prefix:'포항시' },
  146: { sido:'전라북도', prefix:'전주시' },
  155: { sido:'경상남도', prefix:'창원시' },
  232: { sido:'충청남도', prefix:'천안시' },
  // 접두형 보조 관측소 (주 관측소가 따로 있어 pref 로 밀림)
   93: { sido:'강원도', sgg:'춘천시', pref:2 },
  104: { sido:'강원도', sgg:'강릉시', pref:2 },
  181: { sido:'충청북도', prefix:'청주시', pref:2 },       // 2023-05 개소, 학습기간 미달
  255: { sido:'경상남도', prefix:'창원시', pref:2 },
  296: { sido:'부산광역시', all:true,   pref:2 },          // 2023-01 개소, 학습기간 미달
  // 섬 - 해당 도서 시군구에만
  102: { sido:'인천광역시', sgg:'옹진군', island:true },
  115: { sido:'경상북도',   sgg:'울릉군', island:true },
  169: { sido:'전라남도',   sgg:'신안군', island:true },
  185: { sido:'제주특별자치도', sgg:'제주시',   pref:2, island:true },
  188: { sido:'제주특별자치도', sgg:'서귀포시', pref:2, island:true },
  // 산악 - 관내가 맞지만 저지대를 대표하지 못함(플래그)
  100: { sido:'강원도',   sgg:'평창군', highland:true },
  135: { sido:'경상북도', sgg:'김천시', highland:true },
  216: { sido:'강원도',   sgg:'태백시', highland:true },
  // 지점명 != 시군구명
  239: { sido:'세종특별자치시', sgg:'세종' },
  172: { sido:'전라북도', sgg:'고창군' },
  251: { sido:'전라북도', sgg:'고창군', pref:2 },
  177: { sido:'충청남도', sgg:'홍성군' },
  184: { sido:'제주특별자치도', sgg:'제주시' },
  189: { sido:'제주특별자치도', sgg:'서귀포시' },
};

function buildStationMap(stations, sggList) {
  const bySido = {};
  for (const f of sggList) (bySido[SIDO(f.시도)] ||= []).push(String(f.시군구).trim());
  const nameSet = new Map();                       // '시군구' -> [key,...]
  for (const f of sggList) {
    const g = String(f.시군구).trim();
    if (!nameSet.has(g)) nameSet.set(g, []);
    nameSet.get(g).push(KEY(f.시도, g));
  }

  const assign = new Map();                        // 시군구key -> {stn, pref, flags}
  const put = (k, stn, pref, flags) => {
    const cur = assign.get(k);
    if (!cur || pref < cur.pref) assign.set(k, { stn, pref, ...flags });
  };

  for (const s of stations) {
    const id = Number(s.id), nm = String(s.nm).trim();
    const r = STATION_RULES[id];
    const flags = { island: !!(r && r.island), highland: !!(r && r.highland) };
    const pref = (r && r.pref) || 1;

    if (r && r.all) {                              // 광역시 전역
      for (const g of bySido[SIDO(r.sido)] || [])
        if (!(r.except || []).includes(g)) put(KEY(r.sido, g), id, pref, flags);
      continue;
    }
    if (r && r.prefix) {                           // 특례시의 모든 구
      for (const g of bySido[SIDO(r.sido)] || [])
        if (g.startsWith(r.prefix)) put(KEY(r.sido, g), id, pref, flags);
      continue;
    }
    if (r && r.sgg) { put(KEY(r.sido, r.sgg), id, pref, flags); continue; }

    // 규칙이 없으면 지점명 == 시군구명 (접미사 보정)
    let hit = null;
    for (const cand of [nm, nm+'시', nm+'군', nm+'구'])
      if (nameSet.has(cand)) { hit = nameSet.get(cand); break; }
    if (hit) for (const k of hit) put(k, id, pref, flags);
    else console.warn('  [배정실패] 지점', id, nm);
  }

  // 폴백: 관내 지점이 없는 시군구 -> 시도 평균 (섬/산악 지점 제외)
  const sidoPool = {};
  for (const s of stations) {
    const r = STATION_RULES[Number(s.id)];
    if (r && (r.island || r.highland)) continue;
    const k = [...assign.entries()].find(([, v]) => v.stn === Number(s.id));
    if (k) (sidoPool[k[0].split('|')[0]] ||= new Set()).add(Number(s.id));
  }
  const out = [];
  for (const f of sggList) {
    const k = KEY(f.시도, f.시군구), a = assign.get(k);
    const sido = SIDO(f.시도).replace(/\s+/g,'');
    out.push(a
      ? { key:k, 시도:f.시도, 시군구:f.시군구, 방식:'관내지점', 지점:String(a.stn),
          섬:a.island?1:0, 산악:a.highland?1:0 }
      : { key:k, 시도:f.시도, 시군구:f.시군구, 방식:'시도평균',
          지점:[...(sidoPool[sido] || [])].join(';'), 섬:0, 산악:0 });
  }
  return out;
}

/* ---------------------------------------------------------------- 유틸 */
const csv = rows => {
  if (!rows.length) return '';
  const cols = Object.keys(rows[0]);
  const esc = v => v === null || v === undefined ? ''
    : (typeof v === 'string' && /[",\n]/.test(v)) ? '"' + v.replace(/"/g,'""') + '"' : v;
  return cols.join(',') + '\n' + rows.map(r => cols.map(c => esc(r[c])).join(',')).join('\n') + '\n';
};
const write = (name, rows) => {
  fs.writeFileSync(path.join(OUT, name), csv(rows));
  return `${name.padEnd(28)} ${String(rows.length).padStart(8)}행`;
};
// DB 컬럼 `진화시간`('0 days 01:31:00' 형식)의 분 환산. sql/fire_ml_views.sql L335-338 과 동일.
// 정의: 진화시간 = 완진 - 초진. 초진(불길 제압) 이후 완진까지의 실제 진화 소요이며
//       화재 규모의 대리지표로 쓰기 위해 이렇게 정의했다. 원자료 전건 대조로 확인(115,237/115,237).
// 주의: 260831.md §3 의 수치(중앙 17분·120분↑ 5.51%)는 완진-접수 기준이라 이 정의와 다르다.
//       완진-접수 기준은 24시간 초과 오기입이 1,219건인 반면 이 정의는 86건으로 훨씬 깨끗하다.
const DURMIN = v => {
  const m = String(v ?? '').trim().match(/^(?:(\d+)\s*days?\s+)?(\d{1,3}):(\d{2})(?::(\d{2}))?$/);
  return m ? +((+(m[1]||0))*1440 + (+m[2])*60 + (+m[3]) + (m[4] ? +m[4]/60 : 0)).toFixed(2) : null;
};
const dstr = (y,m,d) => `${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;

/* ---------------------------------------------------------------- main */
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const c = await mysql.createConnection(DB);
  const q = async s => (await c.query(s))[0];

  console.log('■ 원천 로드');
  const fire = await q('SELECT 년,월,일,시도,시군구,`재산피해소계(제곱미터)` AS dummy0 FROM `fire_incident_information_std12` LIMIT 0')
    .catch(()=>null); // 컬럼명 확인용 no-op
  const F = await q(
    'SELECT 년,월,일,시도,시군구,사망,부상,`재산피해소계(천원)` AS 피해액,' +
    '`온도(섭씨)` AS 온도,습도,풍향,풍속,발화요인대분류,연소확대물대분류,' +
    '최초착화물대분류,장소중분류,화재유형,진화시간,`소방서거리(km)` AS 소방서거리,출동일시 ' +
    'FROM `fire_incident_information_std12`');
  const W  = await q('SELECT * FROM `tbl_weather_information_std05` WHERE 일시 < "2024-01-01"');
  // 강수는 별도 테이블. 강수 발생일 위주로만 적재되어 있어 미적재일은 0mm 로 간주한다
  // (sql/fire_ml_views.sql 의 v_weather_daily 와 동일한 처리).
  const R  = await q('SELECT 지점, 일시, `일강수량(mm)` AS 강수 FROM `gang_siu.csv_std17` WHERE 일시 < "2024-01-01"');
  const P  = await q('SELECT * FROM `people2_a.csv_std17`');
  const M  = await q('SELECT * FROM `mt_mj_abc17.csv_std17`');
  const SG = await q('SELECT DISTINCT 시도,시군구 FROM `fire_incident_information_std12` ORDER BY 시도,시군구');
  const ST = await q('SELECT DISTINCT 지점 id,지점명 nm FROM `tbl_weather_information_std05`');
  await c.end();
  console.log(`  화재 ${F.length} / 기상 ${W.length} / 강수 ${R.length} / 인구 ${P.length} / 산림 ${M.length} / 시군구 ${SG.length}`);

  /* 1) 기상 지점 배정 */
  console.log('\n■ 기상 지점 배정');
  const smap = buildStationMap(ST, SG);
  const byKey = new Map(smap.map(r => [r.key, r]));
  const cnt = smap.reduce((a,r) => (a[r.방식] = (a[r.방식]||0)+1, a), {});
  console.log('  관내지점', cnt['관내지점']||0, '/ 시도평균 폴백', cnt['시도평균']||0,
              '/ 산악플래그', smap.filter(r=>r.산악).length, '/ 섬플래그', smap.filter(r=>r.섬).length);
  console.log(write('station_map.csv', smap));

  /* 2) 일자별 기상 (지점 -> 시군구) */
  const wByStnDate = new Map(), wBySidoDate = new Map();
  const num = v => (v === null || v === undefined || v === '') ? null : Number(v);
  const stnSido = new Map();
  const rainByStnDate = new Map();
  for (const r of R) rainByStnDate.set(String(r.지점) + '|' + String(r.일시).slice(0,10), Number(r.강수) || 0);
  for (const r of smap) if (r.방식 === '관내지점') stnSido.set(r.지점, SIDO(r.시도).replace(/\s+/g,''));
  for (const r of W) {
    const d = String(r.일시).slice(0,10), s = String(r.지점);
    const rec = {
      기온: num(r['평균기온(°C)']), 최저기온: num(r['최저기온(°C)']), 최고기온: num(r['최고기온(°C)']),
      습도: num(r['평균 상대습도(%)']), 최소습도: num(r['최소 상대습도(%)']),
      풍속: num(r['평균 풍속(m/s)']), 최대풍속: num(r['최대 풍속(m/s)']),
      최대순간풍속: num(r['최대 순간 풍속(m/s)']), 이슬점: num(r['평균 이슬점온도(°C)']),
      증기압: num(r['평균 증기압(hPa)']), 최다풍향: num(r['최다풍향(16방위)']),
      강수량: rainByStnDate.get(s + '|' + d) ?? 0,
      강수실측: rainByStnDate.has(s + '|' + d) ? 1 : 0,
    };
    wByStnDate.set(s + '|' + d, rec);
    const sd = stnSido.get(s);
    if (sd) { const k = sd + '|' + d; (wBySidoDate.get(k) || wBySidoDate.set(k, []).get(k)).push(rec); }
  }
  const avg = arr => {
    const keys = ['기온','최저기온','최고기온','습도','최소습도','풍속','최대풍속','최대순간풍속','이슬점','증기압','최다풍향','강수량','강수실측'];
    const o = {};
    for (const k of keys) { const v = arr.map(x=>x[k]).filter(x=>x!=null); o[k] = v.length ? v.reduce((a,b)=>a+b,0)/v.length : null; }
    return o;
  };
  const weatherFor = (key, d) => {
    const m = byKey.get(key); if (!m) return null;
    if (m.방식 === '관내지점') return wByStnDate.get(m.지점 + '|' + d) || null;
    const pool = (m.지점 || '').split(';').filter(Boolean)
      .map(s => wByStnDate.get(s + '|' + d)).filter(Boolean);
    return pool.length ? avg(pool) : null;
  };

  /* 3) 지역 정적 변수 */
  const pop = new Map();
  for (const r of P) {
    const t = String(r.행정구역).trim().split(/\s+/);
    pop.set(KEY(t[0], t.slice(1).join(' ')), r);
  }
  const forest = new Map();
  for (const r of M) forest.set(KEY(r.시도, r.세부행정구역), r);
  const staticOf = (sido, sgg, year) => {
    const k = KEY(sido, sgg);
    const p = pop.get(k);
    const f = forest.get(k)
         || forest.get(KEY(sido, ROLLUP(sgg)))
         || forest.get(PROFILE_ALIAS[k] || '')
         || forest.get(PROFILE_ALIAS[KEY(sido, ROLLUP(sgg))] || '');
    const 인구 = p ? p[`${year}년_총인구수`] : null;
    const 세대 = p ? p[`${year}년_세대수`] : null;
    const 면적 = f ? Number(f.국토면적_2020) / 100 : null;      // ha -> km²
    return {
      인구, 세대수: 세대,
      평균세대원수: (인구 && 세대) ? +(인구/세대).toFixed(3) : null,
      면적_km2: 면적, 산림율: f ? Number(f.산림율_2020) : null,
      임목축적: f ? Number(f.임목축적_2020) : null,
      인구밀도: (인구 && 면적) ? +(인구/면적).toFixed(1) : null,
    };
  };

  /* 4) ① 발생 - 시군구 x 일 패널 */
  console.log('\n■ ① 발생 패널');
  const fireCnt = new Map();
  const sggAvgDist = new Map();
  for (const r of F) {
    const k = KEY(r.시도, r.시군구), d = dstr(r.년, r.월, r.일);
    fireCnt.set(k + '|' + d, (fireCnt.get(k + '|' + d) || 0) + 1);
    if (r.소방서거리 != null) {
      const a = sggAvgDist.get(k) || [0,0];
      sggAvgDist.set(k, [a[0] + Number(r.소방서거리), a[1] + 1]);
    }
  }
  const dates = [];
  for (let t = Date.UTC(2021,0,1); t <= Date.UTC(2023,11,31); t += 86400000)
    dates.push(new Date(t).toISOString().slice(0,10));

  // 전년도 화재 이력. 당해 연도 값을 쓰면 타깃 누수이므로 직전 연도 집계만 사용한다.
  // 건수는 인구 규모를 다시 끌고 들어오므로 '일 평균 발생률'을 함께 낸다
  // (notebooks/fire_eda_multidim.ipynb Ⅵ장 결론). 2021년 행은 2020 자료가 없어 null.
  const fireByYear = new Map();
  for (const r of F) {
    const yk = KEY(r.시도, r.시군구) + '|' + r.년;
    fireByYear.set(yk, (fireByYear.get(yk) || 0) + 1);
  }
  const daysIn = y => (y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0)) ? 366 : 365;

  // 실효습도(r=0.7, 5일) 계산을 위해 시군구별 습도 시계열을 먼저 구성
  const panel = [];
  for (const s of SG) {
    const k = KEY(s.시도, s.시군구);
    const hist = [];
    let dryRun = 0;                       // 연속 무강수일수 (1mm 미만을 무강수로 간주)
    for (const d of dates) {
      const w = weatherFor(k, d) || {};
      const rain = w.강수량 ?? null;
      dryRun = (rain != null && rain >= 1) ? 0 : dryRun + 1;
      hist.push(w.습도 ?? null);
      const i = hist.length - 1;
      let eff = null, wsum = 0, hsum = 0;
      for (let n = 0; n < 5 && i - n >= 0; n++) {
        const h = hist[i - n]; if (h == null) continue;
        const wt = Math.pow(0.7, n); wsum += wt; hsum += wt * h;
      }
      if (wsum > 0) eff = +( (1 - 0.7) * hsum / (wsum * (1 - 0.7)) ).toFixed(1);

      const y = +d.slice(0,4), mo = +d.slice(5,7), day = +d.slice(8,10);
      const dow = new Date(d + 'T00:00:00Z').getUTCDay();
      const st = staticOf(s.시도, s.시군구, y);
      const dist = sggAvgDist.get(k);
      const mp = byKey.get(k);
      panel.push({
        날짜: d, 시도: s.시도, 시군구: s.시군구,
        화재건수: fireCnt.get(k + '|' + d) || 0,
        년: y, 월: mo, 일: day, 요일: dow, 주말: (dow===0||dow===6)?1:0,
        연중일: Math.round((Date.parse(d) - Date.UTC(y,0,1)) / 86400000) + 1,
        기온: w.기온 ?? null, 최저기온: w.최저기온 ?? null, 최고기온: w.최고기온 ?? null,
        일교차: (w.최고기온!=null && w.최저기온!=null) ? +(w.최고기온-w.최저기온).toFixed(1) : null,
        습도: w.습도 ?? null, 최소습도: w.최소습도 ?? null, 실효습도: eff,
        풍속: w.풍속 ?? null, 최대풍속: w.최대풍속 ?? null, 최대순간풍속: w.최대순간풍속 ?? null,
        이슬점: w.이슬점 ?? null, 증기압: w.증기압 ?? null,
        강수량: rain, 강수실측: w.강수실측 ?? 0, 무강수일수: dryRun,
        ...st,
        평균소방서거리: dist ? +(dist[0]/dist[1]).toFixed(2) : null,
        기상배정방식: mp ? mp.방식 : null, 기상산악지점: mp ? mp.산악 : 0,
        log_인구: st.인구 ? +Math.log(st.인구).toFixed(6) : null,
        // 전년이 자료 범위 밖(2021년 행)이면 null, 범위 안인데 기록이 없으면 0건이다.
        전년화재건수: y > 2021 ? (fireByYear.get(k + '|' + (y-1)) || 0) : null,
        전년화재율: y > 2021
          ? +((fireByYear.get(k + '|' + (y-1)) || 0) / daysIn(y-1)).toFixed(6) : null,
        split: splitOf(d),
      });
    }
  }
  const byS = panel.reduce((a,r)=>(a[r.split]=(a[r.split]||0)+1,a),{});
  const fs_ = panel.reduce((a,r)=>(a[r.split]=(a[r.split]||0)+r.화재건수,a),{});
  console.log('  패널', panel.length, '행  =', SG.length, '시군구 x', dates.length, '일');
  for (const s of ['train','val','test'])
    console.log(`    ${s.padEnd(6)} ${String(byS[s]).padStart(7)}행  화재 ${String(fs_[s]).padStart(6)}건  0셀비율 ${
      (panel.filter(r=>r.split===s&&r.화재건수===0).length/byS[s]*100).toFixed(1)}%`);
  for (const s of ['train','val','test'])
    console.log(write(`occurrence_${s}.csv`, panel.filter(r => r.split === s)));

  // 건 단위 데이터셋에도 '그날 그 지역의 관측 기상'을 붙인다.
  // 사건 기록 기상(온도/습도/풍향/풍속구간)은 현장 기록이라 결측·구간화가 심하고,
  // 관측 기상은 예측 시점에 확보 가능한 값이므로 둘을 함께 노출한다.
  const dailyByKeyDate = new Map();
  for (const r of panel) dailyByKeyDate.set(KEY(r.시도, r.시군구) + '|' + r.날짜, r);

  /* 5) ② 확산 / ③ 원인 - 건 단위 */
  console.log('\n■ ②③ 건 단위');
  const CAUSE = v => v === '방화의심' ? '방화' : v;      // 프로파일 구분 불가 -> 병합
  const ev = [];
  for (const r of F) {
    const d = dstr(r.년, r.월, r.일), k = KEY(r.시도, r.시군구);
    const st = staticOf(r.시도, r.시군구, r.년);
    const hh = String(r.출동일시 || '').slice(11,13);
    const dur = DURMIN(r.진화시간);          // 완진 - 초진 (분)
    const wd = dailyByKeyDate.get(k + '|' + d) || {};
    const dow = new Date(d + 'T00:00:00Z').getUTCDay();
    ev.push({
      날짜: d, 시도: r.시도, 시군구: r.시군구,
      // --- 타깃
      피해액_천원: r.피해액, 연소확대: (r.연소확대물대분류 && r.연소확대물대분류 !== '') ? 1 : 0,
      사망: r.사망, 부상: r.부상, 인명피해: (Number(r.사망)||0) + (Number(r.부상)||0),
      // --- 진화시간(분) = 완진 - 초진. 자원 점유 시간 = 배분 수요 이므로 M4 주 타깃의 원천이다.
      //     피해액 구간을 따라 단조 증가한다(중앙 0분@100만원 미만 -> 55분@1억↑, 120분↑ 0.23% -> 29.33%).
      //     24시간 초과 86건(0.07%)은 완진일시 오기입으로 보고 절단값·플래그를 함께 낸다.
      진화시간_분: dur, 진화시간_분_절단: dur == null ? null : Math.min(dur, 1440),
      진화시간_이상치: dur == null ? null : (dur > 1440 ? 1 : 0),
      원인: CAUSE(r.발화요인대분류),
      // --- 피처
      년: r.년, 월: r.월, 요일: dow, 주말: (dow===0||dow===6)?1:0,
      시각: hh === '' ? null : +hh,
      장소중분류: r.장소중분류, 화재유형: r.화재유형,
      // 최초착화물대분류 제외: 사후 조사 항목이라 예측 시점에 알 수 없다.
      // sql/fire_ml_views.sql 의 M2·M3 누수 차단 원칙과 일치시킨다.
      온도: r.온도, 습도: r.습도, 풍향: r.풍향, 풍속구간: r.풍속,
      일기온: wd.기온 ?? null, 일습도: wd.습도 ?? null, 일최소습도: wd.최소습도 ?? null,
      일풍속: wd.풍속 ?? null, 실효습도: wd.실효습도 ?? null,
      강수량: wd.강수량 ?? null, 무강수일수: wd.무강수일수 ?? null,
      소방서거리: r.소방서거리, ...st,
      log_인구: st.인구 ? +Math.log(st.인구).toFixed(6) : null,
      split: splitOf(d),
    });
  }
  /* 5-1) 대형화재 다축 라벨 (260831.md §3-4)
   * 공식 기준(사망5↑ or 사상자10↑ or 피해 50억↑)은 3년 76건(0.066%)이라 학습 라벨이 될 수 없고,
   * 인명·재산·시간 축은 서로 6.6~18.9% 만 겹쳐 단일 축으로 합칠 수도 없다.
   * 따라서 4축을 각각 별도 컬럼으로 낸다.
   *   주라벨  대형화재_진화120  진화시간 120분 이상    자산가치 중립 · 자원점유 = 배분수요
   *   인명축  대형화재_인명     사상자 1명 이상
   *   재산축  대형화재_재산     유형내 피해액 상위 1%   절대 50억 대신 백분위 -> 자산가치 편향 완화
   *   확산축  연소확대                                 (기존 컬럼)
   * 공식대형은 학습 라벨이 아니라 recall@k 검증용 리트머스로만 쓴다.
   */
  const pct99 = arr => { const v = arr.slice().sort((a,b)=>a-b);
    return v.length ? v[Math.max(0, Math.ceil(v.length*0.99) - 1)] : null; };
  // 임계값은 train 구간에서만 추정해 val/test 에 그대로 적용한다(누수 차단).
  const dmgOf = r => r.피해액_천원 == null ? null : Number(r.피해액_천원);
  const P99ALL = pct99(ev.filter(r=>r.split==='train' && dmgOf(r)!=null).map(dmgOf));
  const P99 = new Map();
  for (const t of new Set(ev.map(r => r.화재유형))) {
    const v = ev.filter(r => r.split==='train' && r.화재유형===t && dmgOf(r)!=null).map(dmgOf);
    if (v.length >= 100) P99.set(t, pct99(v));        // 표본 부족 유형은 전체 기준으로 폴백
  }
  for (const r of ev) {
    const dmg = dmgOf(r), thr = P99.has(r.화재유형) ? P99.get(r.화재유형) : P99ALL;
    // 임계 2종을 함께 낸다. 260831.md 의 5.51% 는 완진-접수 기준값이고,
    // 완진-초진 기준에서 같은 양성률(4.59%)을 주는 임계는 60분이다. 120분은 1.84%.
    r.대형화재_진화120 = r.진화시간_분 == null ? null : (r.진화시간_분 >= 120 ? 1 : 0);
    r.대형화재_진화60  = r.진화시간_분 == null ? null : (r.진화시간_분 >= 60 ? 1 : 0);
    r.대형화재_인명    = r.인명피해 >= 1 ? 1 : 0;
    r.대형화재_재산    = dmg == null ? null : (dmg > thr ? 1 : 0);
    r.공식대형 = ((Number(r.사망)||0) >= 5 || r.인명피해 >= 10 || (dmg||0) >= 5000000) ? 1 : 0;
  }
  console.log('\n■ 대형화재 다축 라벨');
  console.log('  유형내 피해 상위1% 임계(천원, train):',
    [...P99.entries()].map(([t,v]) => `${t}=${v}`).join(' / '), `| 폴백 ${P99ALL}`);
  for (const [nm, key] of [['진화120분↑','대형화재_진화120'], ['진화60분↑','대형화재_진화60'],
                           ['사상자1명↑','대형화재_인명'],
                           ['유형내피해상위1%','대형화재_재산'], ['연소확대','연소확대'],
                           ['공식대형(검증용)','공식대형']]) {
    const line = ['train','val','test'].map(sp => {
      const a = ev.filter(r => r.split===sp && r[key]!=null);
      return `${sp} ${(a.filter(r=>r[key]===1).length/a.length*100).toFixed(2)}%`;
    }).join(' / ');
    console.log(`  ${nm.padEnd(18)} ${line}`);
  }
  const dur = ev.map(r=>r.진화시간_분).filter(v=>v!=null);
  console.log(`  진화시간(완진-초진) 결측 ${ev.length-dur.length}건 / 역전(<0) ${dur.filter(v=>v<0).length}건`
    + ` / 24시간 초과 ${dur.filter(v=>v>1440).length}건`
    + ` (${(dur.filter(v=>v>1440).length/dur.length*100).toFixed(2)}%) / 중앙 ${
      dur.slice().sort((a,b)=>a-b)[Math.floor(dur.length/2)]}분`);
  console.log('  화재유형 분포:', [...ev.reduce((m,r)=>m.set(r.화재유형,(m.get(r.화재유형)||0)+1), new Map())]
    .sort((a,b)=>b[1]-a[1]).map(([t,n])=>`${t} ${n}`).join(' / '));

  // ② 확산: 전건 사용 (원인은 피처로 허용 - 발화 시점에 추정 가능)
  const spread = ev.map(r => ({ ...r, 발화요인: r.원인 }));
  for (const r of spread) delete r.원인;
  // ③ 원인: 미상 제외, 결과 변수 제거
  const cause = ev.filter(r => r.원인 && r.원인 !== '미상')
    .map(({ 피해액_천원, 연소확대, 사망, 부상, 인명피해,
            진화시간_분, 진화시간_분_절단, 진화시간_이상치,
            대형화재_진화120, 대형화재_진화60, 대형화재_인명, 대형화재_재산, 공식대형, ...rest }) => rest);

  for (const [nm, arr] of [['spread', spread], ['cause', cause]]) {
    const b = arr.reduce((a,r)=>(a[r.split]=(a[r.split]||0)+1,a),{});
    console.log(`  ${nm}: train ${b.train} / val ${b.val} / test ${b.test}  (계 ${arr.length})`);
    for (const s of ['train','val','test']) console.log('  ' + write(`${nm}_${s}.csv`, arr.filter(r=>r.split===s)));
  }

  /* 6) ③ 클래스 분포 점검 */
  console.log('\n■ ③ 원인 클래스 분포 (split 별)');
  const cls = [...new Set(cause.map(r=>r.원인))];
  const tab = cls.map(k => {
    const o = { 클래스: k };
    for (const s of ['train','val','test']) o[s] = cause.filter(r=>r.split===s && r.원인===k).length;
    o.계 = o.train + o.val + o.test;
    return o;
  }).sort((a,b)=>b.계-a.계);
  console.table(tab);
  const mn = Math.min(...tab.map(t=>t.train)), mx = Math.max(...tab.map(t=>t.train));
  console.log(`  train 불균형비 ${(mx/mn).toFixed(1)}배 (최다 ${mx} / 최소 ${mn})`);
  console.log(`  전 클래스가 val/test 에 존재: ${tab.every(t=>t.val>0&&t.test>0) ? 'YES' : 'NO'}`);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
