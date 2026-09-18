-- =============================================================================
-- schema_01_views.sql — 서비스 뷰
--
--   대상 DBMS : MariaDB (`ABC8pioneer2`)
--   선행 조건 : schema_00_ddl_all.sql 실행 완료
--   설계 근거 : DB_00 §5-4 · UI_02 SCR-05 · SCR-06
--
--   여기에는 **서비스 조회용 뷰만** 둔다. 학습 데이터셋 생성용 ML 뷰
--   (v_region_master · v_weather_daily · v_ml_occurrence 등 9종)는
--   fire_ml_views.sql 에 있으며 조회 경로에서 사용하지 않는다.
--   이유는 성능이다 — v_ml_occurrence 는 CROSS JOIN + 윈도우 함수라
--   COUNT(*) 조차 7분을 넘긴다. 그래서 검증용 실적은 ref_actual_daily
--   스냅샷 테이블로 따로 적재한다.
--
--   실행: mariadb -h <host> -P <port> -u <user> -p <db> < schema_01_views.sql
-- =============================================================================

SET NAMES utf8mb4;


-- -----------------------------------------------------------------------------
-- v_pred_verification — SCR-05 사후 검증
--
--   outcome 4분류를 뷰에 고정한다. 화면이 각자 CASE 문을 쓰면 같은 날의
--   '헛경보' 정의가 화면마다 달라진다.
--
--   data_status = '데이터없음' 행은 제외한다. 예측을 만들지 못한 지역을
--   '틀렸다'로 세면 성능이 실제보다 나쁘게 보인다. 대신 모수(n)가 251보다
--   작아지므로 화면이 그 차이를 표기해야 한다 (UC6 E5).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_pred_verification AS
SELECT
  p.target_date,
  p.region_cd,
  r.sido,
  r.sigungu,
  p.risk_grade,
  p.occur_probability,
  p.expected_count,
  COALESCE(a.fire_count, 0) AS actual_count,
  CASE
    WHEN p.risk_grade IN ('높음','매우높음') AND COALESCE(a.fire_count,0) >= 1 THEN 'hit'
    WHEN p.risk_grade IN ('높음','매우높음') AND COALESCE(a.fire_count,0) =  0 THEN 'false_alarm'
    WHEN p.risk_grade IN ('낮음','보통')     AND COALESCE(a.fire_count,0) >= 1 THEN 'miss'
    ELSE 'correct_reject'
  END AS outcome
FROM pred_daily p
JOIN ref_region_profile r
  ON r.region_cd = p.region_cd
LEFT JOIN ref_actual_daily a
  ON a.target_date = p.target_date
 AND a.region_cd   = p.region_cd
WHERE p.is_current = 1
  AND p.data_status <> '데이터없음';


-- -----------------------------------------------------------------------------
-- v_pred_high_days — SCR-06 상시 고위험 지역
--
--   '높음 이상' 의 정의를 한 곳에 고정한다 (BR-HRK-01).
--   데이터없음 행도 포함한다 — 분모(총 일수)에는 들어가고 분자(is_high)에는
--   들어가지 않아야 비율이 정직해진다.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_pred_high_days AS
SELECT
  p.region_cd,
  r.sido,
  r.sigungu,
  p.target_date,
  p.risk_grade,
  p.expected_count,
  p.expected_damage_load,
  CASE WHEN p.risk_grade IN ('높음','매우높음') THEN 1 ELSE 0 END AS is_high
FROM pred_daily p
JOIN ref_region_profile r
  ON r.region_cd = p.region_cd
WHERE p.is_current = 1;


-- -----------------------------------------------------------------------------
-- v_batch_run_health — SCR-07 최근 실행 요약 (신규 제안)
--
--   운영 화면이 매번 같은 가공을 반복하지 않도록 뷰로 내린다.
--   '오늘을 넘지 않는 대상일' 필터는 조회 시점에 따라 달라지므로 뷰에 넣지
--   않는다 — 애플리케이션이 target_date <= CURDATE() 로 건다 (UC9 E4).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_batch_run_health AS
SELECT
  b.run_id,
  b.target_date,
  b.status,
  b.regions_ok,
  b.regions_failed,
  b.fallback_source,
  b.started_at,
  b.finished_at,
  TIMESTAMPDIFF(SECOND, b.started_at, b.finished_at) AS elapsed_sec,
  (SELECT COUNT(*) FROM ops_batch_region_failure f WHERE f.run_id = b.run_id) AS failure_rows,
  (SELECT COUNT(*) FROM ops_feature_drift d WHERE d.run_id = b.run_id AND d.alert_flag = 1) AS drift_alerts_cnt
FROM ops_batch_run b;


-- -----------------------------------------------------------------------------
-- v_model_acceptance_summary — SCR-07 모델 목록 · SCR-08 진입 (신규 제안)
--
--   등록부와 판정표를 붙여 "왜 참고용인지" 를 한 줄로 만든다.
--   판정표가 아직 적재되지 않은 모델은 criteria_total = 0 으로 나온다 —
--   화면은 이를 '판정 미기록' 으로 표기하고 '합격' 으로 읽지 않는다.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_model_acceptance_summary AS
SELECT
  m.model_version,
  m.model_id,
  m.algorithm,
  m.is_active,
  m.acceptance_passed,
  COUNT(a.criterion_code)                                        AS criteria_total,
  SUM(CASE WHEN a.verdict = 'PASS' THEN 1 ELSE 0 END)            AS criteria_passed,
  SUM(CASE WHEN a.verdict = 'FAIL' THEN 1 ELSE 0 END)            AS criteria_failed,
  GROUP_CONCAT(CASE WHEN a.verdict = 'FAIL'
                    THEN CONCAT(a.criterion_desc, ' (', a.threshold_expr, ')')
               END ORDER BY a.sort_order SEPARATOR ' · ')        AS failed_reasons
FROM ops_model_registry m
LEFT JOIN ops_model_acceptance a
  ON a.model_version = m.model_version
GROUP BY m.model_version, m.model_id, m.algorithm, m.is_active, m.acceptance_passed;

-- =============================================================================
-- 끝. 뷰 생성 확인:
--   SELECT TABLE_NAME FROM information_schema.VIEWS
--    WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME;
-- =============================================================================
