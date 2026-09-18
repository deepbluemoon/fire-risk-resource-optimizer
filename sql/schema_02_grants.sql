-- =============================================================================
-- schema_02_grants.sql — 권한 부여
--
--   대상 DBMS : MariaDB (`ABC8pioneer2`)
--   설계 근거 : BP_00 §2 계층별 쓰기 주체 · DB_00 §6 화면-테이블 매핑
--
-- 주의 — 이 파일에는 계정 생성과 비밀번호가 없다.
--   프로젝트 절대규칙에 따라 자격증명은 코드·문서·SQL 에 쓰지 않고
--   .env / FIRE_DB_* 환경변수로만 다룬다. 계정은 DBA 가 별도로 만든다:
--       CREATE USER 'fire_batch'@'%' IDENTIFIED BY '<비밀번호는 여기 쓰지 않는다>';
--   아래 GRANT 문은 계정이 이미 존재할 때 실행한다.
--
-- 계정 설계 — 권한을 프로세스 경계에 맞춘다
--   fire_batch : P1 야간 산출 + UC10 학습.  예측·운영 테이블에 쓴다.
--   fire_api   : P2·P3 조회 API.            원칙적으로 SELECT 만.
--                예외는 SCR-04 배분 조정 3테이블 — 사용자가 데이터를 만드는
--                유일한 지점이라 여기만 INSERT 를 연다.
--   fire_ro    : 분석·감사용 읽기 전용.
--
--   DB 이름은 배포 환경에 맞춰 치환한다. 아래는 ABC8pioneer2 기준이다.
--
--   실행: mariadb -h <host> -P <port> -u <admin> -p < schema_02_grants.sql
-- =============================================================================

SET NAMES utf8mb4;

-- -----------------------------------------------------------------------------
-- 1) 배치·학습 계정 — 쓰기 주체
-- -----------------------------------------------------------------------------
-- L3 예측 산출물
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.pred_daily             TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.pred_feature_snapshot  TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.pred_cause_top3        TO 'fire_batch'@'%';

-- L4 실행 기록
GRANT SELECT, INSERT, UPDATE         ON ABC8pioneer2.ops_batch_run              TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE         ON ABC8pioneer2.ops_batch_step             TO 'fire_batch'@'%';
GRANT SELECT, INSERT                 ON ABC8pioneer2.ops_batch_region_failure   TO 'fire_batch'@'%';
GRANT SELECT, INSERT                 ON ABC8pioneer2.ops_feature_drift          TO 'fire_batch'@'%';

-- L4 모델 거버넌스 (UC10 학습이 쓴다)
GRANT SELECT, INSERT, UPDATE         ON ABC8pioneer2.ops_model_registry      TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ops_model_acceptance    TO 'fire_batch'@'%';

-- L2 참조 (학습이 갱신: 등급 경계·군집·인코딩 맵 / 배치가 갱신: 실적·배정)
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_region_profile   TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_station_map      TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_region_cluster   TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_risk_grade       TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_category_map     TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_actual_daily     TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_actual_spread    TO 'fire_batch'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ABC8pioneer2.ref_actual_cause     TO 'fire_batch'@'%';

-- 학습 데이터셋 생성에 필요한 ML 뷰 (읽기)
GRANT SELECT ON ABC8pioneer2.v_region_master     TO 'fire_batch'@'%';
GRANT SELECT ON ABC8pioneer2.v_weather_daily     TO 'fire_batch'@'%';
GRANT SELECT ON ABC8pioneer2.v_station_map       TO 'fire_batch'@'%';
GRANT SELECT ON ABC8pioneer2.v_ml_occurrence     TO 'fire_batch'@'%';
GRANT SELECT ON ABC8pioneer2.v_ml_spread         TO 'fire_batch'@'%';
GRANT SELECT ON ABC8pioneer2.v_ml_cause          TO 'fire_batch'@'%';


-- -----------------------------------------------------------------------------
-- 2) 조회 API 계정 — 원칙적으로 SELECT 만
--    "조회 API 는 SELECT 만 한다" 는 아키텍처 결정을 권한으로 강제한다.
-- -----------------------------------------------------------------------------
GRANT SELECT ON ABC8pioneer2.ref_region_profile        TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ref_station_map           TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ref_region_cluster        TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ref_risk_grade            TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ref_actual_daily          TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ref_actual_spread         TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ref_actual_cause          TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.pred_daily                TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.pred_feature_snapshot     TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.pred_cause_top3           TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_batch_run             TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_batch_step            TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_batch_region_failure  TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_feature_drift         TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_model_registry        TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_model_acceptance      TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_deployment_status     TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.ops_constraint            TO 'fire_api'@'%';

-- 서비스 뷰
GRANT SELECT ON ABC8pioneer2.v_pred_verification        TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.v_pred_high_days           TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.v_batch_run_health         TO 'fire_api'@'%';
GRANT SELECT ON ABC8pioneer2.v_model_acceptance_summary TO 'fire_api'@'%';

-- SCR-04 배분 조정 — 사용자가 데이터를 만드는 유일한 지점이므로 여기만 쓰기를 연다.
-- UPDATE·DELETE 는 주지 않는다. 조정 이력은 수정·삭제되지 않는다 (SC-009).
GRANT SELECT, INSERT ON ABC8pioneer2.ops_allocation_proposal          TO 'fire_api'@'%';
GRANT SELECT, INSERT ON ABC8pioneer2.ops_allocation_proposal_item     TO 'fire_api'@'%';
GRANT SELECT, INSERT ON ABC8pioneer2.ops_allocation_adjustment        TO 'fire_api'@'%';
GRANT SELECT, INSERT ON ABC8pioneer2.ops_allocation_adjustment_item   TO 'fire_api'@'%';


-- -----------------------------------------------------------------------------
-- 3) 감사·분석 읽기 전용 계정
-- -----------------------------------------------------------------------------
GRANT SELECT ON ABC8pioneer2.* TO 'fire_ro'@'%';


-- -----------------------------------------------------------------------------
-- 4) 반영
-- -----------------------------------------------------------------------------
FLUSH PRIVILEGES;

-- 확인:
--   SHOW GRANTS FOR 'fire_batch'@'%';
--   SHOW GRANTS FOR 'fire_api'@'%';
-- =============================================================================
