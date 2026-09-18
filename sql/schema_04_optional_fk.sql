-- =============================================================================
-- schema_04_optional_fk.sql — 외래키 추가 (선택 · 검토 후 실행)
--
--   대상 DBMS : MariaDB (`ABC8pioneer2`)
--   설계 근거 : DB_00 §5-5 · §10-1 (FK 미선언은 이번 검토의 최대 구조 차이)
--
-- 왜 별도 파일인가
--   운영 스키마에는 현재 외래키가 하나도 없다. 참조 무결성이 전적으로
--   배치·API 코드에 맡겨져 있다. 의도적 선택일 수 있으므로(대량 적재 성능)
--   기본 DDL 에 넣지 않고 이 파일로 분리했다.
--
-- 실행 전 반드시 확인할 것
--   1) §1 사전 점검을 먼저 돌린다. 고아 행이 하나라도 있으면 ALTER 가 실패한다.
--   2) 컬럼 캐릭터셋·콜레이션이 참조 대상과 같아야 한다.
--      이 DB 에는 utf8mb4_uca1400_ai_ci 와 utf8mb4_unicode_ci 가 섞여 있어
--      실제로 조인에서 'Illegal mix of collations' 가 난 전례가 있다.
--      §1-3 으로 먼저 확인한다.
--   3) 대량 적재(pred_daily 하루 1,757행 x 실행 횟수) 성능에 영향을 준다.
--      배치 시간이 늘어나는지 스테이징에서 먼저 측정한다.
--
--   되돌리기: 파일 끝 §3 의 DROP 문을 쓴다.
-- =============================================================================

SET NAMES utf8mb4;

-- =============================================================================
-- §1. 사전 점검 — 아래가 모두 0행이어야 §2 를 실행할 수 있다
-- =============================================================================

-- 1-1. 마스터에 없는 지역 코드를 쓰는 행
SELECT 'pred_daily' AS tbl, COUNT(*) AS orphans FROM pred_daily p
  LEFT JOIN ref_region_profile m ON m.region_cd = p.region_cd WHERE m.region_cd IS NULL
UNION ALL SELECT 'ref_station_map', COUNT(*) FROM ref_station_map t
  LEFT JOIN ref_region_profile m ON m.region_cd = t.region_cd WHERE m.region_cd IS NULL
UNION ALL SELECT 'ref_region_cluster', COUNT(*) FROM ref_region_cluster t
  LEFT JOIN ref_region_profile m ON m.region_cd = t.region_cd WHERE m.region_cd IS NULL
UNION ALL SELECT 'ref_actual_daily', COUNT(*) FROM ref_actual_daily t
  LEFT JOIN ref_region_profile m ON m.region_cd = t.region_cd WHERE m.region_cd IS NULL
UNION ALL SELECT 'ops_deployment_status', COUNT(*) FROM ops_deployment_status t
  LEFT JOIN ref_region_profile m ON m.region_cd = t.region_cd WHERE m.region_cd IS NULL
UNION ALL SELECT 'ops_allocation_proposal_item', COUNT(*) FROM ops_allocation_proposal_item t
  LEFT JOIN ref_region_profile m ON m.region_cd = t.region_cd WHERE m.region_cd IS NULL;

-- 1-2. 없는 실행·모델·배분안을 가리키는 행
SELECT 'pred_daily.run_id' AS ref_col, COUNT(*) AS orphans FROM pred_daily p
  LEFT JOIN ops_batch_run r ON r.run_id = p.run_id WHERE r.run_id IS NULL
UNION ALL SELECT 'pred_daily.source_model_version', COUNT(*) FROM pred_daily p
  LEFT JOIN ops_model_registry g ON g.model_version = p.source_model_version
 WHERE p.source_model_version IS NOT NULL AND g.model_version IS NULL
UNION ALL SELECT 'ref_risk_grade.model_version', COUNT(*) FROM ref_risk_grade t
  LEFT JOIN ops_model_registry g ON g.model_version = t.model_version WHERE g.model_version IS NULL
UNION ALL SELECT 'ops_allocation_adjustment.proposal_id', COUNT(*) FROM ops_allocation_adjustment a
  LEFT JOIN ops_allocation_proposal p ON p.proposal_id = a.proposal_id WHERE p.proposal_id IS NULL;

-- 1-3. 콜레이션 불일치 확인 (참조 컬럼끼리 같아야 한다)
SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND COLUMN_NAME IN ('region_cd','model_version','run_id','proposal_id','adjustment_id')
 ORDER BY COLUMN_NAME, TABLE_NAME;


-- =============================================================================
-- §2. 외래키 추가
--    ON DELETE 정책
--      RESTRICT — 지역 마스터·실행 기록은 참조가 남아 있으면 지우지 못한다.
--                 예측 이력은 보존이 원칙이므로(FR-022) CASCADE 를 쓰지 않는다.
--      CASCADE  — 배분안/조정의 상세 항목만. 헤더가 사라지면 항목은 의미가 없다.
-- =============================================================================

-- L2 참조 → 지역 마스터
ALTER TABLE ref_station_map
  ADD CONSTRAINT fk_station_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ref_region_cluster
  ADD CONSTRAINT fk_cluster_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE,
  ADD CONSTRAINT fk_cluster_model  FOREIGN KEY (model_version)
      REFERENCES ops_model_registry (model_version) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ref_risk_grade
  ADD CONSTRAINT fk_grade_model FOREIGN KEY (model_version)
      REFERENCES ops_model_registry (model_version) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ref_category_map
  ADD CONSTRAINT fk_catmap_model FOREIGN KEY (model_version)
      REFERENCES ops_model_registry (model_version) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ref_actual_daily
  ADD CONSTRAINT fk_actual_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ref_actual_spread
  ADD CONSTRAINT fk_aspread_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ref_actual_cause
  ADD CONSTRAINT fk_acause_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

-- L3 예측 → 지역·실행·모델
ALTER TABLE pred_daily
  ADD CONSTRAINT fk_pred_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE,
  ADD CONSTRAINT fk_pred_run    FOREIGN KEY (run_id)
      REFERENCES ops_batch_run (run_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  ADD CONSTRAINT fk_pred_model  FOREIGN KEY (source_model_version)
      REFERENCES ops_model_registry (model_version) ON DELETE RESTRICT ON UPDATE CASCADE;

-- 스냅샷·원인은 예측 행에 종속된다. 예측이 사라지면 남을 이유가 없다.
ALTER TABLE pred_feature_snapshot
  ADD CONSTRAINT fk_snap_pred FOREIGN KEY (target_date, region_cd, run_id)
      REFERENCES pred_daily (target_date, region_cd, run_id) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE pred_cause_top3
  ADD CONSTRAINT fk_cause_pred FOREIGN KEY (target_date, region_cd, run_id)
      REFERENCES pred_daily (target_date, region_cd, run_id) ON DELETE CASCADE ON UPDATE CASCADE;

-- L4 운영
ALTER TABLE ops_deployment_status
  ADD CONSTRAINT fk_deploy_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ops_constraint
  ADD CONSTRAINT fk_constraint_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ops_allocation_proposal
  ADD CONSTRAINT fk_proposal_run FOREIGN KEY (run_id)
      REFERENCES ops_batch_run (run_id) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ops_allocation_proposal_item
  ADD CONSTRAINT fk_item_proposal FOREIGN KEY (proposal_id)
      REFERENCES ops_allocation_proposal (proposal_id) ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT fk_item_region   FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ops_allocation_adjustment
  ADD CONSTRAINT fk_adj_proposal FOREIGN KEY (proposal_id)
      REFERENCES ops_allocation_proposal (proposal_id) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ops_allocation_adjustment_item
  ADD CONSTRAINT fk_adjitem_adj    FOREIGN KEY (adjustment_id)
      REFERENCES ops_allocation_adjustment (adjustment_id) ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT fk_adjitem_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

-- 신규 4테이블
ALTER TABLE ops_model_acceptance
  ADD CONSTRAINT fk_acceptance_model FOREIGN KEY (model_version)
      REFERENCES ops_model_registry (model_version) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE ops_batch_step
  ADD CONSTRAINT fk_step_run FOREIGN KEY (run_id)
      REFERENCES ops_batch_run (run_id) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE ops_batch_region_failure
  ADD CONSTRAINT fk_bfail_run    FOREIGN KEY (run_id)
      REFERENCES ops_batch_run (run_id) ON DELETE CASCADE ON UPDATE CASCADE,
  ADD CONSTRAINT fk_bfail_region FOREIGN KEY (region_cd)
      REFERENCES ref_region_profile (region_cd) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE ops_feature_drift
  ADD CONSTRAINT fk_drift_run FOREIGN KEY (run_id)
      REFERENCES ops_batch_run (run_id) ON DELETE CASCADE ON UPDATE CASCADE;


-- =============================================================================
-- §3. 되돌리기
-- =============================================================================
-- ALTER TABLE ref_station_map                 DROP FOREIGN KEY fk_station_region;
-- ALTER TABLE ref_region_cluster              DROP FOREIGN KEY fk_cluster_region;
-- ALTER TABLE ref_region_cluster              DROP FOREIGN KEY fk_cluster_model;
-- ALTER TABLE ref_risk_grade                  DROP FOREIGN KEY fk_grade_model;
-- ALTER TABLE ref_category_map                DROP FOREIGN KEY fk_catmap_model;
-- ALTER TABLE ref_actual_daily                DROP FOREIGN KEY fk_actual_region;
-- ALTER TABLE ref_actual_spread               DROP FOREIGN KEY fk_aspread_region;
-- ALTER TABLE ref_actual_cause                DROP FOREIGN KEY fk_acause_region;
-- ALTER TABLE pred_daily                      DROP FOREIGN KEY fk_pred_region;
-- ALTER TABLE pred_daily                      DROP FOREIGN KEY fk_pred_run;
-- ALTER TABLE pred_daily                      DROP FOREIGN KEY fk_pred_model;
-- ALTER TABLE pred_feature_snapshot           DROP FOREIGN KEY fk_snap_pred;
-- ALTER TABLE pred_cause_top3                 DROP FOREIGN KEY fk_cause_pred;
-- ALTER TABLE ops_deployment_status           DROP FOREIGN KEY fk_deploy_region;
-- ALTER TABLE ops_constraint                  DROP FOREIGN KEY fk_constraint_region;
-- ALTER TABLE ops_allocation_proposal         DROP FOREIGN KEY fk_proposal_run;
-- ALTER TABLE ops_allocation_proposal_item    DROP FOREIGN KEY fk_item_proposal;
-- ALTER TABLE ops_allocation_proposal_item    DROP FOREIGN KEY fk_item_region;
-- ALTER TABLE ops_allocation_adjustment       DROP FOREIGN KEY fk_adj_proposal;
-- ALTER TABLE ops_allocation_adjustment_item  DROP FOREIGN KEY fk_adjitem_adj;
-- ALTER TABLE ops_allocation_adjustment_item  DROP FOREIGN KEY fk_adjitem_region;
-- ALTER TABLE ops_model_acceptance            DROP FOREIGN KEY fk_acceptance_model;
-- ALTER TABLE ops_batch_step                  DROP FOREIGN KEY fk_step_run;
-- ALTER TABLE ops_batch_region_failure        DROP FOREIGN KEY fk_bfail_run;
-- ALTER TABLE ops_batch_region_failure        DROP FOREIGN KEY fk_bfail_region;
-- ALTER TABLE ops_feature_drift               DROP FOREIGN KEY fk_drift_run;
-- =============================================================================
