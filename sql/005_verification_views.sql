-- 사후 검증 뷰 (data-model.md §4) — FR-018 · FR-019 · FR-020 · SC-008
SET NAMES utf8mb4;

CREATE OR REPLACE VIEW v_pred_verification AS
SELECT
  p.target_date,
  p.region_cd,
  r.sido,
  r.sigungu,
  p.risk_grade,
  p.occur_probability,
  p.expected_count,
  COALESCE(a.fire_count, 0)                                   AS actual_count,
  CASE
    WHEN p.risk_grade IN ('높음','매우높음') AND COALESCE(a.fire_count,0) >= 1 THEN 'hit'
    WHEN p.risk_grade IN ('높음','매우높음') AND COALESCE(a.fire_count,0) =  0 THEN 'false_alarm'
    WHEN p.risk_grade IN ('낮음','보통')     AND COALESCE(a.fire_count,0) >= 1 THEN 'miss'
    ELSE 'correct_reject'
  END                                                          AS outcome
FROM pred_daily p
JOIN ref_region_profile r ON r.region_cd = p.region_cd
LEFT JOIN ref_actual_daily a
       ON a.target_date = p.target_date AND a.region_cd = p.region_cd
WHERE p.is_current = 1 AND p.data_status <> '데이터없음';

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
JOIN ref_region_profile r ON r.region_cd = p.region_cd
WHERE p.is_current = 1;
