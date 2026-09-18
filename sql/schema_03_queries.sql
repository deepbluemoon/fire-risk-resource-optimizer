-- =============================================================================
-- schema_03_queries.sql — 점검·운영 조회 쿼리 모음 (읽기 전용)
--
--   대상 DBMS : MariaDB (`ABC8pioneer2`)
--   설계 근거 : DB_00 §7 무결성 규칙 R1~R9 · §6 화면-테이블 매핑
--
--   이 파일은 데이터를 바꾸지 않는다. 전부 SELECT 다.
--   배치 후처리나 운영 점검에서 통째로 돌려 결과를 확인한다:
--       mariadb -h <host> -P <port> -u <user> -p <db> -t < schema_03_queries.sql
--
--   판정 규칙 — 아래 R1~R9 는 **결과가 0행이어야 정상**이다
--   (R7·R9 는 값을 읽어 판단한다).
-- =============================================================================

SET NAMES utf8mb4;

-- =============================================================================
-- §0. 생성 확인 — 구조가 제대로 올라왔는지
-- =============================================================================

-- 0-1. 테이블 23개가 있는지
SELECT TABLE_NAME, TABLE_ROWS, ROUND(DATA_LENGTH/1024/1024, 1) AS data_mb
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_TYPE = 'BASE TABLE'
   AND (TABLE_NAME LIKE 'ref\_%' OR TABLE_NAME LIKE 'pred\_%' OR TABLE_NAME LIKE 'ops\_%')
 ORDER BY TABLE_NAME;

-- 0-2. 뷰 목록
SELECT TABLE_NAME
  FROM information_schema.VIEWS
 WHERE TABLE_SCHEMA = DATABASE()
 ORDER BY TABLE_NAME;


-- =============================================================================
-- §1. 무결성 점검 (DB_00 §7)
-- =============================================================================

-- R1 [FR-022] 같은 (일자, 지역)에 현재본이 둘 이상 있으면 안 된다
--     재산출 트랜잭션이 이전 행을 is_current=0 으로 내리지 못한 경우 잡힌다.
SELECT 'R1' AS rule_id, target_date, region_cd, COUNT(*) AS cur_rows
  FROM pred_daily
 WHERE is_current = 1
 GROUP BY target_date, region_cd
HAVING COUNT(*) > 1;

-- R2 [SC-006] 각 대상일의 현재본은 251행이어야 한다
--     251행 원칙이 깨지면 SCR-01 목록에서 지역이 조용히 사라진다.
SELECT 'R2' AS rule_id, target_date, COUNT(*) AS rows_cnt
  FROM pred_daily
 WHERE is_current = 1
 GROUP BY target_date
HAVING COUNT(*) <> 251;

-- R3 [FR-040~043 · SC-016] 사후한정(C) 피처가 입력에 섞이면 안 된다
--     결과가 나오면 검증 성능만 부풀고 운영에서 무너진다.
SELECT 'R3' AS rule_id, target_date, region_cd, run_id
  FROM pred_feature_snapshot
 WHERE CAST(feature_grade AS CHAR) LIKE '%"C"%'
 LIMIT 50;

-- R4 [BR-RSK-03] 미산출 지표를 0 으로 채운 흔적
--     NULL 이어야 할 자리에 0 이 들어가면 화면이 '0% 위험' 으로 읽는다.
SELECT 'R4' AS rule_id, target_date, COUNT(*) AS zero_rows
  FROM pred_daily
 WHERE is_current = 1
   AND (spread_probability = 0 OR longburn_probability = 0)
 GROUP BY target_date;

-- R5 [FR-039] 사유 없는 조정 (CHECK 로 막고 있으나 이관·복구 시 점검)
SELECT 'R5' AS rule_id, adjustment_id, author_id, created_at
  FROM ops_allocation_adjustment
 WHERE reason IS NULL OR CHAR_LENGTH(TRIM(reason)) = 0;

-- R6 [FR-036 · G13] 저장된 조정이 활성 제약을 위반하는지
--     API 가 422 로 막지만, 이력에 남은 값도 사후 점검한다.
SELECT 'R6' AS rule_id,
       ai.adjustment_id, ai.region_cd, c.constraint_type,
       c.value AS limit_value, ai.adjusted_headcount AS attempted
  FROM ops_allocation_adjustment_item ai
  JOIN ops_constraint c
    ON (c.region_cd = ai.region_cd OR c.region_cd IS NULL)
   AND c.is_active = 1
 WHERE ai.adjusted_headcount IS NOT NULL
   AND (   (c.constraint_type = '정원상한' AND ai.adjusted_headcount > c.value)
        OR (c.constraint_type = '최소잔류' AND ai.adjusted_headcount < c.value));

-- R7 [FR-008 · CS-04] 등급 경계가 [0,1] 을 빈틈없이 덮는지 (값을 읽어 판단)
--     covered 가 1.0 에서 멀거나 lo<>0 · hi<>1 이면 등급 변환에 구멍이 있다.
SELECT 'R7' AS rule_id,
       model_version,
       MIN(prob_lower) AS lo,
       MAX(prob_upper) AS hi,
       ROUND(SUM(prob_upper - prob_lower), 6) AS covered,
       COUNT(*) AS grade_cnt
  FROM ref_risk_grade
 GROUP BY model_version;

-- R8 [구조] 고아 예측 — FK 가 없으므로 쿼리로 대신 본다
SELECT 'R8' AS rule_id, p.run_id, COUNT(*) AS orphan_rows
  FROM pred_daily p
  LEFT JOIN ops_batch_run r ON r.run_id = p.run_id
 WHERE r.run_id IS NULL
 GROUP BY p.run_id;

-- R8b [구조] 마스터에 없는 지역 코드
SELECT 'R8b' AS rule_id, p.region_cd, COUNT(*) AS rows_cnt
  FROM pred_daily p
  LEFT JOIN ref_region_profile m ON m.region_cd = p.region_cd
 WHERE m.region_cd IS NULL
 GROUP BY p.region_cd;

-- R9 [FR-024 · CS-01] 폴백으로 마감했는데 사유가 없는 실행
SELECT 'R9' AS rule_id, run_id, target_date, status, fallback_source
  FROM ops_batch_run
 WHERE status = '폴백' AND fallback_source IS NULL;


-- =============================================================================
-- §2. 화면별 대표 조회 — API 가 실제로 쓰는 형태
--     :date, :from, :to, :regionCd 는 실행 시 치환한다.
-- =============================================================================

-- SCR-01 오늘 위험도 대시보드 — 기본 조회일 결정 (오늘을 넘지 않는 최근 예측일)
SELECT MAX(target_date) AS default_date
  FROM pred_daily
 WHERE is_current = 1
   AND target_date <= CURDATE();

-- SCR-01 위험 순위 목록 — 데이터없음은 뒤로 민다 (BR-RSK-01)
SELECT p.region_cd, r.sido, r.sigungu, p.risk_rank, p.risk_grade,
       p.occur_probability, p.expected_count, p.confidence, p.data_status
  FROM pred_daily p
  JOIN ref_region_profile r ON r.region_cd = p.region_cd
 WHERE p.target_date = (SELECT MAX(target_date) FROM pred_daily
                         WHERE is_current = 1 AND target_date <= CURDATE())
   AND p.is_current = 1
 ORDER BY (p.data_status = '데이터없음'), p.expected_count DESC, r.sido, r.sigungu
 LIMIT 30;

-- SCR-01 신선도 배너 (CS-01) — 대상일의 최신 실행
SELECT run_id, status, fallback_source, started_at, finished_at, forecast_fetched_at
  FROM ops_batch_run
 WHERE target_date = CURDATE()
 ORDER BY run_id DESC
 LIMIT 1;

-- SCR-01 사용 가능한 정렬 축 (미적재 축을 비활성화하기 위한 판정, CS-10)
SELECT SUM(expected_count       IS NOT NULL) AS has_count,
       SUM(occur_probability    IS NOT NULL) AS has_probability,
       SUM(expected_damage_load IS NOT NULL) AS has_damage,
       SUM(spread_probability   IS NOT NULL) AS has_spread,
       SUM(longburn_probability IS NOT NULL) AS has_longburn
  FROM pred_daily
 WHERE is_current = 1
   AND target_date = (SELECT MAX(target_date) FROM pred_daily
                       WHERE is_current = 1 AND target_date <= CURDATE());

-- SCR-05 사후 검증 요약 — 높음 이상 4분면 + 모수
SELECT outcome, COUNT(*) AS cnt
  FROM v_pred_verification
 WHERE target_date BETWEEN '2023-01-01' AND '2023-12-31'
 GROUP BY outcome
 ORDER BY FIELD(outcome, 'hit','false_alarm','miss','correct_reject');

-- SCR-05 월별 캘리브레이션 — 예측 합계 / 실제 합계 (SC-011 기준 ±15%)
SELECT DATE_FORMAT(v.target_date, '%Y-%m') AS ym,
       ROUND(SUM(v.expected_count), 2)     AS pred_sum,
       SUM(v.actual_count)                 AS actual_sum,
       ROUND(SUM(v.expected_count) / NULLIF(SUM(v.actual_count), 0), 4) AS calib_ratio
  FROM v_pred_verification v
 WHERE v.target_date BETWEEN '2023-01-01' AND '2023-12-31'
 GROUP BY ym
 ORDER BY ym;

-- SCR-06 상시 고위험 지역 순위 — 일수만이 아니라 비율을 함께 낸다 (BR-HRK-02)
SELECT region_cd, sido, sigungu,
       SUM(is_high)                          AS high_days,
       COUNT(*)                              AS total_days,
       ROUND(SUM(is_high) / COUNT(*), 4)     AS ratio,
       ROUND(SUM(COALESCE(expected_damage_load, 0)), 4) AS damage_sum
  FROM v_pred_high_days
 WHERE target_date BETWEEN '2023-01-01' AND '2023-12-31'
 GROUP BY region_cd, sido, sigungu
 ORDER BY high_days DESC
 LIMIT 20;

-- SCR-07 활성 모델과 판정 요약
SELECT model_id, model_version, is_active, acceptance_passed,
       criteria_total, criteria_passed, criteria_failed, failed_reasons
  FROM v_model_acceptance_summary
 ORDER BY is_active DESC, model_id;

-- SCR-09 실행 단계 타임라인 (특정 run_id)
SELECT s.step_no, s.step_code, s.status, s.started_at, s.finished_at,
       TIMESTAMPDIFF(SECOND, s.started_at, s.finished_at) AS elapsed_sec
  FROM ops_batch_step s
 WHERE s.run_id = (SELECT MAX(run_id) FROM ops_batch_run)
 ORDER BY s.step_no;

-- SCR-09 자주 실패하는 지역 (재발 감지 — JSON 으로는 못 하던 집계)
SELECT f.region_cd, r.sigungu, COUNT(*) AS fail_runs,
       MAX(f.occurred_at) AS last_failed, MAX(f.reason) AS last_reason
  FROM ops_batch_region_failure f
  JOIN ref_region_profile r ON r.region_cd = f.region_cd
 GROUP BY f.region_cd, r.sigungu
 ORDER BY fail_runs DESC
 LIMIT 20;


-- =============================================================================
-- §3. 용량·보존 점검 (DB_00 §8 · §9)
-- =============================================================================

-- 3-1. 예측 계층 증가 추이 (월별 적재 행 수)
SELECT DATE_FORMAT(target_date, '%Y-%m') AS ym,
       COUNT(*)                          AS pred_rows,
       COUNT(DISTINCT run_id)            AS runs,
       SUM(is_current)                   AS current_rows
  FROM pred_daily
 GROUP BY ym
 ORDER BY ym DESC
 LIMIT 12;

-- 3-2. 보존 기간(3년)을 넘긴 이력 — 아카이브 대상
--      주의: pred_daily 와 pred_feature_snapshot 은 같은 run_id 로 짝을 유지해야
--      SC-005 재구성이 성립한다. 한쪽만 지우지 않는다.
SELECT 'pred_daily' AS tbl, COUNT(*) AS old_rows
  FROM pred_daily
 WHERE is_current = 0 AND target_date < DATE_SUB(CURDATE(), INTERVAL 3 YEAR)
UNION ALL
SELECT 'pred_feature_snapshot', COUNT(*)
  FROM pred_feature_snapshot
 WHERE target_date < DATE_SUB(CURDATE(), INTERVAL 3 YEAR);

-- 3-3. 예측/스냅샷 짝 정합 (한쪽만 있으면 재구성 불가)
SELECT 'pred_without_snapshot' AS kind, COUNT(*) AS cnt
  FROM pred_daily p
  LEFT JOIN pred_feature_snapshot s
    ON s.target_date = p.target_date AND s.region_cd = p.region_cd AND s.run_id = p.run_id
 WHERE s.run_id IS NULL AND p.data_status = '정상'
UNION ALL
SELECT 'snapshot_without_pred', COUNT(*)
  FROM pred_feature_snapshot s
  LEFT JOIN pred_daily p
    ON p.target_date = s.target_date AND p.region_cd = s.region_cd AND p.run_id = s.run_id
 WHERE p.run_id IS NULL;

-- =============================================================================
-- 끝.
-- =============================================================================
