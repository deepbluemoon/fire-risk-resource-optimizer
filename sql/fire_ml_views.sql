-- =============================================================================
-- 화재 예측 모델용 Feature / Target View 세트
-- Target DB : ABC8pioneer2 (MariaDB 12.1)
-- 근거 문서 : Intent-Specify.md  [설계 원리] 발생 → 확산 → 배분 파이프라인
--
-- 지원 모델
--   M1 발생률 회귀 (Poisson + 노출 offset)  -> v_ml_occurrence
--   M2 확산 규모 회귀 (로그 피해액/Tweedie) -> v_ml_spread
--   M3 발화 원인 유형 분류 (다중 클래스)    -> v_ml_cause
--
-- 누수 차단 원칙
--   사후 조사 항목(발화요인·최초착화물·연소확대물·피해액·사상자·진화시간·
--   출동/도착/귀소일시·그을음면적)은 target 으로만 노출하고 feature 에서 제외한다.
-- =============================================================================



-- -----------------------------------------------------------------------------
-- [지원 0] v_fire_incident : 화재 원자료 중복 제거 레이어
--   2026-08-31 확인: fire_incident_information_std12 에 동일 CSV 가 2회 적재되어
--   115,237건이 230,474건으로 정확히 배증되어 있음 (id 1..115237 == 115238..230474).
--   베이스 테이블은 수정하지 않고, 전 업무컬럼 기준 GROUP BY 로 중복을 접는다.
--   원본이 정상화(중복 삭제)되어도 이 뷰의 결과는 동일하므로 그대로 사용 가능하다.
--   기준 건수 115,237 은 미복제 테이블 fire_incident_information_std05 와 일치.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_fire_incident AS
SELECT
  MIN(id) AS id,
  년, 월, 일, 시도, 시군구, 사망, 부상,
  `재산피해소계(천원)`, `그을음면적(제곱미터)`,
  `온도(섭씨)`, 습도, 풍향, 풍속,
  발화요인대분류, 연소확대물대분류, 최초착화물대분류, 장소중분류,
  진화시간, `소방서거리(km)`, `119지역대거리(km)`,
  출동일시, 도착일시, 귀소일시
FROM fire_incident_information_std12
GROUP BY 년, 월, 일, 시도, 시군구, 사망, 부상,
         `재산피해소계(천원)`, `그을음면적(제곱미터)`,
         `온도(섭씨)`, 습도, 풍향, 풍속,
         발화요인대분류, 연소확대물대분류, 최초착화물대분류, 장소중분류,
         진화시간, `소방서거리(km)`, `119지역대거리(km)`,
         출동일시, 도착일시, 귀소일시;




-- -----------------------------------------------------------------------------
-- [지원 1] v_region_master : 251개 시군구 지역 마스터 + 정적 위험 프로파일
--   행정구역 표기 정규화(강원특별자치도->강원도, 전북특별자치도->전라북도)
--   특례시 일반구(고양시덕양구 등)는 보조데이터가 시 단위로만 존재하므로
--   부모 시로 폴백 조인한다. 폴백 사용 여부는 profile_join_level 로 노출.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_region_master AS
WITH base AS (
  SELECT DISTINCT
         시도   COLLATE utf8mb4_unicode_ci AS sido_raw,
         시군구 COLLATE utf8mb4_unicode_ci AS sgg
  FROM fire_incident_information_std12
), norm AS (
  SELECT
    sido_raw,
    sgg,
    CASE sido_raw
      WHEN '강원특별자치도' THEN '강원도'
      WHEN '전북특별자치도' THEN '전라북도'
      ELSE sido_raw
    END AS sido_std,
    -- 보조데이터 조인용 시도 (군위군은 2023년 대구 편입, 보조데이터는 경북 수록)
    CASE WHEN sido_raw = '대구광역시' AND sgg = '군위군' THEN '경상북도'
         WHEN sido_raw = '강원특별자치도' THEN '강원도'
         WHEN sido_raw = '전북특별자치도' THEN '전라북도'
         ELSE sido_raw END AS sido_join,
    -- 보조데이터 조인용 시군구 (특례시 일반구 -> 부모 시, 세종 -> 세종시)
    CASE WHEN sgg = '세종' THEN '세종시'
         WHEN sgg LIKE '%시%구' THEN CONCAT(SUBSTRING_INDEX(sgg, '시', 1), '시')
         ELSE sgg END AS sgg_join
  FROM base
)
SELECT
  CONCAT(n.sido_std, '|', n.sgg)                    AS region_key,
  n.sido_raw,
  n.sido_std,
  n.sgg,
  n.sido_join,
  n.sgg_join,
  CASE WHEN n.sgg <> n.sgg_join THEN 'parent_city' ELSE 'exact' END AS profile_join_level,
  mt.국토면적_2020                                   AS land_area,
  mt.산림면적_2020                                   AS forest_area,
  mt.산림율_2020                                     AS forest_ratio,
  mt.임목축적_2020                                   AS growing_stock,
  mt.평균_임목축적_2020                              AS growing_stock_avg,
  ig.인구밀도                                        AS pop_density,
  pp.`2021년_총인구수`                               AS pop_2021,
  pp.`2022년_총인구수`                               AS pop_2022,
  pp.`2023년_총인구수`                               AS pop_2023,
  pp.`2024년_총인구수`                               AS pop_2024,
  pp.`2021년_세대수`                                 AS hh_2021,
  pp.`2022년_세대수`                                 AS hh_2022,
  pp.`2023년_세대수`                                 AS hh_2023,
  pp.`2024년_세대수`                                 AS hh_2024
FROM norm n
LEFT JOIN `mt_mj_abc17.csv_std17`  mt ON mt.시도   = n.sido_join AND mt.세부행정구역 = n.sgg_join
LEFT JOIN `igmj_abc17.csv_std17`   ig ON ig.시도명 = n.sido_join AND ig.시군구명     = n.sgg_join
LEFT JOIN `people2_a.csv_std17`    pp ON pp.행정구역 = CONCAT(n.sido_join, ' ', n.sgg_join);


-- -----------------------------------------------------------------------------
-- [지원 2] v_station_map : 시군구 -> 기상 관측 지점 배정 (FR-006)
--   관측 지점 97개 < 시군구 251개 이므로 3단계 규칙으로 배정하고,
--   대리 지점 사용 여부를 is_proxy_station / station_match_tier 로 기록한다.
--     tier 1 = 지점명 == 시군구명            (예: 경주시)
--     tier 2 = 지점명 == 시군구명 접미사 제거 (예: 강릉시 -> 강릉)
--     tier 3 = 시도 대표 지점 폴백 (도청 소재지 기준)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_sido_proxy_station AS
SELECT '서울특별시' AS sido_std, '서울' AS station_name UNION ALL
SELECT '부산광역시', '부산'           UNION ALL
SELECT '대구광역시', '대구'           UNION ALL
SELECT '인천광역시', '인천'           UNION ALL
SELECT '광주광역시', '광주'           UNION ALL
SELECT '대전광역시', '대전'           UNION ALL
SELECT '울산광역시', '울산'           UNION ALL
SELECT '세종특별자치시', '세종'       UNION ALL
SELECT '경기도', '수원'               UNION ALL
SELECT '강원도', '춘천'               UNION ALL
SELECT '충청북도', '청주'             UNION ALL
SELECT '충청남도', '홍성'             UNION ALL
SELECT '전라북도', '전주'             UNION ALL
SELECT '전라남도', '목포'             UNION ALL
SELECT '경상북도', '안동'             UNION ALL
SELECT '경상남도', '창원'             UNION ALL
SELECT '제주특별자치도', '제주';

CREATE OR REPLACE VIEW v_station_map AS
WITH st AS (
  SELECT DISTINCT 지점 AS station_id, 지점명 AS station_name
  FROM tbl_weather_information_std05
)
SELECT
  r.region_key,
  r.sido_std,
  r.sgg,
  COALESCE(e.station_id, s.station_id, p.station_id)     AS station_id,
  COALESCE(e.station_name, s.station_name, p.station_name) AS station_name,
  CASE WHEN e.station_id IS NOT NULL THEN 1
       WHEN s.station_id IS NOT NULL THEN 2
       ELSE 3 END                                        AS station_match_tier,
  CASE WHEN e.station_id IS NOT NULL OR s.station_id IS NOT NULL
       THEN 0 ELSE 1 END                                 AS is_proxy_station
FROM v_region_master r
LEFT JOIN st e ON e.station_name = r.sgg
LEFT JOIN st s ON s.station_name = LEFT(r.sgg, CHAR_LENGTH(r.sgg) - 1)
              AND RIGHT(r.sgg, 1) IN ('시', '군')
LEFT JOIN v_sido_proxy_station px ON px.sido_std = r.sido_std
LEFT JOIN st p ON p.station_name = px.station_name;


-- -----------------------------------------------------------------------------
-- [지원 3] v_weather_daily : 지점·일 기상 관측 + 강수 + 도메인 파생지표 (FR-009)
--   강수 테이블은 강수 발생일 위주로만 적재되어 있으므로 미적재일은 0mm 로 간주
--   (precip_observed 로 실측/보정 구분).
--   파생: 실효습도, 체감온도, 풍속-습도 결합지수, 연속 무강수일수
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_weather_daily AS
WITH base AS (
  SELECT
    w.지점                        AS station_id,
    w.지점명                      AS station_name,
    CAST(w.일시 AS DATE)          AS obs_date,
    w.`평균기온(°C)`              AS temp_avg,
    w.`최저기온(°C)`              AS temp_min,
    w.`최고기온(°C)`              AS temp_max,
    w.`평균 상대습도(%)`          AS hum_avg,
    w.`최소 상대습도(%)`          AS hum_min,
    w.`평균 풍속(m/s)`            AS wind_avg,
    w.`최대 풍속(m/s)`            AS wind_max,
    w.`최대 순간 풍속(m/s)`       AS wind_gust,
    w.`평균 이슬점온도(°C)`       AS dewpoint_avg,
    w.`평균 증기압(hPa)`          AS vapor_pressure,
    COALESCE(g.`일강수량(mm)`, 0) AS precip_mm,
    CASE WHEN g.지점 IS NULL THEN 0 ELSE 1 END AS precip_observed
  FROM tbl_weather_information_std05 w
  LEFT JOIN `gang_siu.csv_std17` g
         ON g.지점 = w.지점 AND g.일시 = w.일시
), lagged AS (
  SELECT b.*,
    LAG(b.hum_avg, 1) OVER (PARTITION BY b.station_id ORDER BY b.obs_date) AS h1,
    LAG(b.hum_avg, 2) OVER (PARTITION BY b.station_id ORDER BY b.obs_date) AS h2,
    LAG(b.hum_avg, 3) OVER (PARTITION BY b.station_id ORDER BY b.obs_date) AS h3,
    LAG(b.hum_avg, 4) OVER (PARTITION BY b.station_id ORDER BY b.obs_date) AS h4,
    SUM(CASE WHEN b.precip_mm >= 1 THEN 1 ELSE 0 END)
      OVER (PARTITION BY b.station_id ORDER BY b.obs_date ROWS UNBOUNDED PRECEDING) AS rain_grp
  FROM base b
)
SELECT
  l.station_id, l.station_name, l.obs_date,
  l.temp_avg, l.temp_min, l.temp_max,
  l.hum_avg, l.hum_min,
  l.wind_avg, l.wind_max, l.wind_gust,
  l.dewpoint_avg, l.vapor_pressure,
  l.precip_mm, l.precip_observed,
  -- 일교차
  (l.temp_max - l.temp_min)                                        AS temp_range,
  -- 실효습도 He = 0.3*(H0 + 0.7H1 + 0.49H2 + 0.343H3 + 0.2401H4), r=0.7 5일 절단
  ROUND(0.3 * (l.hum_avg + 0.7*l.h1 + 0.49*l.h2 + 0.343*l.h3 + 0.2401*l.h4), 2)
                                                                   AS eff_humidity,
  -- 체감온도(기상청 겨울철 공식). 조건 미충족 시 기온 그대로.
  CASE WHEN l.temp_avg <= 10 AND l.wind_avg * 3.6 > 4.8
       THEN ROUND(13.12 + 0.6215*l.temp_avg
                  - 11.37*POW(l.wind_avg*3.6, 0.16)
                  + 0.3965*l.temp_avg*POW(l.wind_avg*3.6, 0.16), 2)
       ELSE ROUND(l.temp_avg, 2) END                                AS apparent_temp,
  -- 풍속-습도 결합지수 : 건조할수록·바람 강할수록 큼
  ROUND(l.wind_avg * (100 - l.hum_min) / 100, 3)                    AS wind_dryness_index,
  -- 연속 무강수일수 (1mm 미만을 무강수로 간주). 강수일 = 0
  (ROW_NUMBER() OVER (PARTITION BY l.station_id, l.rain_grp ORDER BY l.obs_date) - 1)
                                                                   AS dry_days
FROM lagged l;


-- -----------------------------------------------------------------------------
-- [지원 4] v_fire_daily : 시군구·일 화재 집계 (M1 target 원천)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_fire_daily AS
SELECT
  CONCAT(CASE f.시도 COLLATE utf8mb4_unicode_ci
           WHEN '강원특별자치도' THEN '강원도'
           WHEN '전북특별자치도' THEN '전라북도'
           ELSE f.시도 COLLATE utf8mb4_unicode_ci END,
         '|', f.시군구 COLLATE utf8mb4_unicode_ci)                  AS region_key,
  CAST(CONCAT(f.년,'-',LPAD(f.월,2,'0'),'-',LPAD(f.일,2,'0')) AS DATE) AS fire_date,
  COUNT(*)                                                          AS fire_cnt,
  SUM(f.사망)                                                       AS death_cnt,
  SUM(f.부상)                                                       AS injury_cnt,
  SUM(f.`재산피해소계(천원)`)                                        AS damage_krw_k,
  SUM(f.`그을음면적(제곱미터)`)                                      AS soot_area,
  SUM(CASE WHEN f.연소확대물대분류 IS NOT NULL
            AND f.연소확대물대분류 <> '' THEN 1 ELSE 0 END)          AS spread_cnt
FROM v_fire_incident f
GROUP BY 1, 2;


-- =============================================================================
-- [M1] v_ml_occurrence : 발생률 회귀 학습셋
--   학습 단위 = 시군구 × 일 전체 조합 (발생 0건 포함), 2021-01-01 ~ 2023-12-31
--   target    = fire_cnt (Poisson), 보조 target = fire_occurred (이진)
--   offset    = ln_exposure (연도별 총인구 로그) -> 규모가 다른 지역 간 비교 가능
--   누수 차단 = 당일 화재 결과값(피해액·사상자·확대) 미포함,
--               이력 파생은 전년도 집계만 사용
-- =============================================================================
CREATE OR REPLACE VIEW v_ml_occurrence AS
WITH cal AS (
  SELECT DISTINCT CAST(일시 AS DATE) AS d
  FROM tbl_weather_information_std05
  WHERE 일시 >= '2021-01-01' AND 일시 < '2024-01-01'
), region_year AS (
  SELECT region_key, YEAR(fire_date) AS yr, SUM(fire_cnt) AS cnt
  FROM v_fire_daily GROUP BY 1, 2
)
SELECT
  -- ---- 식별자 ----
  r.region_key, r.sido_std, r.sgg, c.d AS target_date,
  sm.station_id, sm.station_name, sm.station_match_tier, sm.is_proxy_station,

  -- ---- TARGET ----
  COALESCE(fd.fire_cnt, 0)                                   AS y_fire_cnt,
  CASE WHEN COALESCE(fd.fire_cnt,0) > 0 THEN 1 ELSE 0 END    AS y_fire_occurred,

  -- ---- OFFSET (노출 규모) ----
  CASE YEAR(c.d) WHEN 2021 THEN r.pop_2021
                 WHEN 2022 THEN r.pop_2022
                 ELSE r.pop_2023 END                         AS exposure_pop,
  LN(NULLIF(CASE YEAR(c.d) WHEN 2021 THEN r.pop_2021
                           WHEN 2022 THEN r.pop_2022
                           ELSE r.pop_2023 END, 0))          AS ln_exposure,

  -- ---- FEATURE : 기상 관측 ----
  wd.temp_avg, wd.temp_min, wd.temp_max, wd.temp_range,
  wd.hum_avg, wd.hum_min,
  wd.wind_avg, wd.wind_max, wd.wind_gust,
  wd.dewpoint_avg, wd.vapor_pressure,
  wd.precip_mm, wd.precip_observed,

  -- ---- FEATURE : 기상 파생 ----
  wd.eff_humidity, wd.apparent_temp, wd.wind_dryness_index, wd.dry_days,

  -- ---- FEATURE : 시간 주기성 ----
  YEAR(c.d)                                                  AS f_year,
  MONTH(c.d)                                                 AS f_month,
  DAYOFYEAR(c.d)                                             AS f_doy,
  DAYOFWEEK(c.d)                                             AS f_dow,
  CASE WHEN DAYOFWEEK(c.d) IN (1, 7) THEN 1 ELSE 0 END       AS f_is_weekend,
  CASE WHEN MONTH(c.d) IN (3,4,5)   THEN '봄'
       WHEN MONTH(c.d) IN (6,7,8)   THEN '여름'
       WHEN MONTH(c.d) IN (9,10,11) THEN '가을'
       ELSE '겨울' END                                       AS f_season,

  -- ---- FEATURE : 지역 위험 프로파일 ----
  r.land_area, r.forest_area, r.forest_ratio, r.growing_stock,
  r.pop_density, r.profile_join_level,

  -- ---- FEATURE : 이력 (전년도만 사용 = 누수 없음) ----
  ry.cnt                                                     AS prior_year_fire_cnt,
  ROUND(ry.cnt / 365.0, 4)                                   AS prior_year_daily_rate

FROM v_region_master r
CROSS JOIN cal c
LEFT JOIN v_station_map sm ON sm.region_key = r.region_key
LEFT JOIN v_weather_daily wd ON wd.station_id = sm.station_id AND wd.obs_date = c.d
LEFT JOIN v_fire_daily fd ON fd.region_key = r.region_key AND fd.fire_date = c.d
LEFT JOIN region_year ry ON ry.region_key = r.region_key AND ry.yr = YEAR(c.d) - 1;


-- =============================================================================
-- [M2] v_ml_spread : 확산 규모 회귀 학습셋
--   학습 단위 = 실제 발생한 화재 1건 (115,237)
--   target    = y_damage_krw_k / y_log_damage / y_spread_flag / y_casualty_flag
--   누수 차단 = 발화요인·최초착화물·연소확대물·진화시간·출동/도착/귀소일시·
--               그을음면적·사상자는 feature 에서 제외 (target 또는 미노출)
-- =============================================================================
CREATE OR REPLACE VIEW v_ml_spread AS
SELECT
  -- ---- 식별자 ----
  f.id                                                        AS incident_id,
  r.region_key, r.sido_std, r.sgg,
  CAST(CONCAT(f.년,'-',LPAD(f.월,2,'0'),'-',LPAD(f.일,2,'0')) AS DATE) AS fire_date,

  -- ---- TARGET ----
  f.`재산피해소계(천원)`                                       AS y_damage_krw_k,
  ROUND(LN(1 + f.`재산피해소계(천원)`), 6)                     AS y_log_damage,
  CASE WHEN f.연소확대물대분류 IS NOT NULL
        AND f.연소확대물대분류 <> '' THEN 1 ELSE 0 END          AS y_spread_flag,
  CASE WHEN COALESCE(f.사망,0) + COALESCE(f.부상,0) > 0
       THEN 1 ELSE 0 END                                       AS y_casualty_flag,
  f.`그을음면적(제곱미터)`                                      AS y_soot_area,
  -- 진화시간(분). '0 days 01:31:00' 형식 파싱
  CASE WHEN f.진화시간 LIKE '% days %'
       THEN CAST(SUBSTRING_INDEX(f.진화시간,' ',1) AS SIGNED) * 1440
            + TIME_TO_SEC(SUBSTRING_INDEX(f.진화시간,' ',-1)) / 60
       ELSE NULL END                                           AS y_suppress_min,

  -- ---- FEATURE : 사건 기록 기상 (신고 시점 관측치) ----
  f.`온도(섭씨)`                                               AS x_temp_at_incident,
  f.습도                                                       AS x_hum_at_incident,
  f.풍향                                                       AS x_wind_dir_at_incident,
  CASE f.풍속 WHEN '0~4 m/s'    THEN 2.0
              WHEN '5~8 m/s'    THEN 6.5
              WHEN '9~12 m/s'   THEN 10.5
              WHEN '13~17 m/s'  THEN 15.0
              WHEN '18 m/s 이상' THEN 20.0
              ELSE NULL END                                    AS x_wind_at_incident,

  -- ---- FEATURE : 일 단위 관측 기상 (지점 배정 기준) ----
  wd.temp_avg, wd.temp_min, wd.temp_max, wd.temp_range,
  wd.hum_avg, wd.hum_min, wd.wind_avg, wd.wind_max, wd.wind_gust,
  wd.precip_mm, wd.eff_humidity, wd.apparent_temp,
  wd.wind_dryness_index, wd.dry_days,

  -- ---- FEATURE : 대응 여건 (사전 확정된 지리 정보) ----
  f.`소방서거리(km)`                                           AS x_dist_fire_station,
  f.`119지역대거리(km)`                                        AS x_dist_119_unit,
  f.장소중분류                                                 AS x_place_type,

  -- ---- FEATURE : 시간 주기성 ----
  f.년 AS f_year, f.월 AS f_month,
  DAYOFWEEK(CAST(CONCAT(f.년,'-',LPAD(f.월,2,'0'),'-',LPAD(f.일,2,'0')) AS DATE)) AS f_dow,
  CASE WHEN f.월 IN (3,4,5) THEN '봄' WHEN f.월 IN (6,7,8) THEN '여름'
       WHEN f.월 IN (9,10,11) THEN '가을' ELSE '겨울' END       AS f_season,

  -- ---- FEATURE : 지역 위험 프로파일 ----
  r.land_area, r.forest_area, r.forest_ratio, r.growing_stock, r.pop_density,
  sm.station_match_tier, sm.is_proxy_station

FROM v_fire_incident f
JOIN v_region_master r
  ON r.sido_raw = f.시도 COLLATE utf8mb4_unicode_ci
 AND r.sgg      = f.시군구 COLLATE utf8mb4_unicode_ci
LEFT JOIN v_station_map sm ON sm.region_key = r.region_key
LEFT JOIN v_weather_daily wd
       ON wd.station_id = sm.station_id
      AND wd.obs_date = CAST(CONCAT(f.년,'-',LPAD(f.월,2,'0'),'-',LPAD(f.일,2,'0')) AS DATE);


-- =============================================================================
-- [M3] v_ml_cause : 발화 원인 유형 분류 학습셋
--   학습 단위 = 실제 발생한 화재 1건 (115,237)
--   target    = y_cause (발화요인대분류 12종)
--   누수 차단 = 최초착화물·연소확대물은 원인의 동의어/결과이므로 feature 제외.
--               피해액·사상자·진화시간 등 결과 항목도 제외.
--   '미상'(10,189건)은 학습 시 제외 여부를 모델 단계에서 결정하도록 플래그 제공.
-- =============================================================================
CREATE OR REPLACE VIEW v_ml_cause AS
SELECT
  -- ---- 식별자 ----
  f.id                                                        AS incident_id,
  r.region_key, r.sido_std, r.sgg,
  CAST(CONCAT(f.년,'-',LPAD(f.월,2,'0'),'-',LPAD(f.일,2,'0')) AS DATE) AS fire_date,

  -- ---- TARGET ----
  f.발화요인대분류                                             AS y_cause,
  CASE WHEN f.발화요인대분류 = '미상' THEN 1 ELSE 0 END         AS y_cause_unknown,

  -- ---- FEATURE : 사건 기록 기상 ----
  f.`온도(섭씨)`                                               AS x_temp_at_incident,
  f.습도                                                       AS x_hum_at_incident,
  f.풍향                                                       AS x_wind_dir_at_incident,
  CASE f.풍속 WHEN '0~4 m/s'    THEN 2.0
              WHEN '5~8 m/s'    THEN 6.5
              WHEN '9~12 m/s'   THEN 10.5
              WHEN '13~17 m/s'  THEN 15.0
              WHEN '18 m/s 이상' THEN 20.0
              ELSE NULL END                                    AS x_wind_at_incident,

  -- ---- FEATURE : 일 단위 관측 기상 ----
  wd.temp_avg, wd.temp_min, wd.temp_max, wd.temp_range,
  wd.hum_avg, wd.hum_min, wd.wind_avg, wd.wind_max,
  wd.precip_mm, wd.eff_humidity, wd.apparent_temp,
  wd.wind_dryness_index, wd.dry_days,

  -- ---- FEATURE : 발생 맥락 ----
  f.장소중분류                                                 AS x_place_type,
  f.`소방서거리(km)`                                           AS x_dist_fire_station,

  -- ---- FEATURE : 시간 주기성 ----
  f.년 AS f_year, f.월 AS f_month,
  DAYOFWEEK(CAST(CONCAT(f.년,'-',LPAD(f.월,2,'0'),'-',LPAD(f.일,2,'0')) AS DATE)) AS f_dow,
  CASE WHEN f.월 IN (3,4,5) THEN '봄' WHEN f.월 IN (6,7,8) THEN '여름'
       WHEN f.월 IN (9,10,11) THEN '가을' ELSE '겨울' END       AS f_season,

  -- ---- FEATURE : 지역 위험 프로파일 ----
  r.land_area, r.forest_area, r.forest_ratio, r.growing_stock, r.pop_density

FROM v_fire_incident f
JOIN v_region_master r
  ON r.sido_raw = f.시도 COLLATE utf8mb4_unicode_ci
 AND r.sgg      = f.시군구 COLLATE utf8mb4_unicode_ci
LEFT JOIN v_station_map sm ON sm.region_key = r.region_key
LEFT JOIN v_weather_daily wd
       ON wd.station_id = sm.station_id
      AND wd.obs_date = CAST(CONCAT(f.년,'-',LPAD(f.월,2,'0'),'-',LPAD(f.일,2,'0')) AS DATE);
