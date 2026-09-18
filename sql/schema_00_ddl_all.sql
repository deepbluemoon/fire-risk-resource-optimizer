-- =============================================================================
-- schema_00_ddl_all.sql — 전체 구조 생성 DDL (단일 파일)
--
--   대상 시스템 : 화재위험 예측 및 소방 자원 배분 지원 시스템
--   대상 DBMS   : MariaDB (운영 DB `ABC8pioneer2`)
--   설계 근거   : docs/usecases/DB_00_데이터베이스구조설계_*.md
--                 UC_00 · BP_00 · UI_00 · UI_01 · UI_02
--   생성 대상   : 테이블 23개 (기존 19 + 화면 요구 신규 4) + 인덱스
--
-- 실행 방법
--   mariadb -h <host> -P <port> -u <user> -p <database> < schema_00_ddl_all.sql
--   자격증명은 .env / FIRE_DB_* 환경변수를 사용한다. 이 파일에 쓰지 않는다.
--
-- 성질
--   - 전 문장이 CREATE TABLE IF NOT EXISTS 이므로 여러 번 실행해도 안전하다.
--   - 이미 배포된 19개 테이블은 그대로 두고 넘어간다(구조를 바꾸지 않는다).
--   - 인덱스는 기존 테이블에 이미 있으므로 CREATE TABLE 안에 함께 둔다.
--   - 외래키는 이 파일에 넣지 않는다. 운영 스키마의 현재 관행과 같으며,
--     추가하려면 schema_04_optional_fk.sql 을 먼저 검토한다.
--
-- 실행 순서 (파일 내부에서 이미 이 순서로 배치되어 있다)
--   1) L4 뿌리   ops_model_registry · ops_batch_run
--   2) L2 마스터 ref_region_profile
--   3) L2 참조   station_map · cluster · risk_grade · category_map · actual_*
--   4) L3 예측   pred_daily · pred_feature_snapshot · pred_cause_top3
--   5) L4 운영   deployment_status · constraint · allocation_* · 신규 4
-- =============================================================================

SET NAMES utf8mb4;


-- =============================================================================
-- 1) L4 뿌리 — 다른 계층이 참조하는 실행·모델 기록
-- =============================================================================

-- 모델 등록부. 어떤 모델이 활성이고 합격했는지의 단일 출처 (FR-043 · SC-016)
CREATE TABLE IF NOT EXISTS ops_model_registry (
  model_version     VARCHAR(40) NOT NULL                       COMMENT '모델 버전 식별자',
  model_id          ENUM('M0','M1','M5','M2a','M3','M4') NOT NULL,
  algorithm         VARCHAR(60)  NULL,
  artifact_path     VARCHAR(255) NULL,
  train_data_range  JSON         NULL                          COMMENT '{from,to}',
  feature_list      JSON         NULL                          COMMENT '입력 변수 + 관측가능성 등급',
  cv_metrics        JSON         NULL                          COMMENT 'Expanding-window 5폴드',
  holdout_metrics   JSON         NULL                          COMMENT '홀드아웃 2023 (1회 개봉)',
  acceptance_passed TINYINT(1)   NOT NULL DEFAULT 0            COMMENT '합격 판정 (SC-010)',
  code_commit       VARCHAR(40)  NULL,
  is_active         TINYINT(1)   NOT NULL DEFAULT 0,
  created_at        DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (model_version),
  KEY ix_active (model_id, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='모델 등록부 — SCR-07 · SCR-08 조회원';

-- 배치 실행 기록. SCR-01 신선도 배너와 SCR-07/09 의 상태 원천 (FR-003 · FR-025)
CREATE TABLE IF NOT EXISTS ops_batch_run (
  run_id              BIGINT AUTO_INCREMENT,
  target_date         DATE        NOT NULL,
  started_at          DATETIME(3) NOT NULL,
  finished_at         DATETIME(3) NULL,
  status              ENUM('실행중','성공','부분실패','실패','폴백') NOT NULL DEFAULT '실행중',
  regions_ok          SMALLINT    NOT NULL DEFAULT 0,
  regions_failed      SMALLINT    NOT NULL DEFAULT 0,
  failure_detail      JSON        NULL                         COMMENT '지역별 실패 요약(정규화본은 ops_batch_region_failure)',
  forecast_fetched_at DATETIME    NULL,
  fallback_source     ENUM('전일결과','M0기저율') NULL          COMMENT 'CS-01 폴백 배너 사유',
  drift_alerts        JSON        NULL                         COMMENT '입력 분포 변화(정규화본은 ops_feature_drift)',
  PRIMARY KEY (run_id),
  KEY ix_target (target_date, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='배치 실행 기록 — 실패를 조용히 넘기지 않는다 (FR-025)';


-- =============================================================================
-- 2) L2 마스터 — 251개 시군구
-- =============================================================================

CREATE TABLE IF NOT EXISTS ref_region_profile (
  region_cd               VARCHAR(24)   NOT NULL,
  sido                    VARCHAR(30)   NOT NULL,
  sigungu                 VARCHAR(30)   NOT NULL,
  area_km2                DECIMAL(12,3) NULL,
  forest_ratio            DECIMAL(8,4)  NULL,
  timber_volume           DECIMAL(14,2) NULL,
  population              INT           NULL,
  households              INT           NULL,
  pop_density             DECIMAL(12,2) NULL,
  log_population          DECIMAL(10,6) NULL                   COMMENT 'Poisson offset',
  avg_station_distance_km DECIMAL(8,2)  NULL                   COMMENT '소방서 평균 거리',
  PRIMARY KEY (region_cd),
  KEY ix_sido (sido)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='시군구 마스터 251행 — 전 화면의 지역 축';


-- =============================================================================
-- 3) L2 참조 — 배정·룩업·실적
-- =============================================================================

-- 기상 관측지점 배정. 대리 사용 33.5% 는 화면에 드러난다 (FR-006 · CS-05)
CREATE TABLE IF NOT EXISTS ref_station_map (
  region_cd     VARCHAR(24) NOT NULL,
  station_id    VARCHAR(16) NULL,
  assign_method ENUM('관내','시도평균','산악','도서') NOT NULL DEFAULT '관내',
  grid_nx       SMALLINT   NULL,
  grid_ny       SMALLINT   NULL,
  is_proxy      TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (region_cd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='시군구 → 관측지점 배정 (SCR-02 신뢰도 표기 근거)';

CREATE TABLE IF NOT EXISTS ref_region_cluster (
  region_cd     VARCHAR(24) NOT NULL,
  model_version VARCHAR(40) NOT NULL,
  cluster_id    TINYINT     NOT NULL,
  cluster_label VARCHAR(40) NULL,
  PRIMARY KEY (region_cd, model_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='k=6 지역 군집 — 모델 버전별로 한 벌';

-- 등급 경계값. 화면마다 달라지지 않게 DB 가 단일 출처다 (FR-008 · CS-04)
CREATE TABLE IF NOT EXISTS ref_risk_grade (
  model_version  VARCHAR(40)  NOT NULL,
  grade          ENUM('낮음','보통','높음','매우높음') NOT NULL,
  prob_lower     DECIMAL(7,6) NOT NULL,
  prob_upper     DECIMAL(7,6) NOT NULL,
  sort_order     TINYINT      NOT NULL,
  color_token    VARCHAR(32)  NULL,
  description_ko VARCHAR(300) NULL,
  PRIMARY KEY (model_version, grade),
  CONSTRAINT ck_grade_bounds
    CHECK (prob_lower >= 0 AND prob_upper <= 1 AND prob_lower < prob_upper)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='위험 등급 확률 경계 — SCR-01 · SCR-07';

-- 범주 인코딩 맵. 학습 전용 범주가 추론에서 터지지 않도록 unknown 버킷을 둔다
CREATE TABLE IF NOT EXISTS ref_category_map (
  model_version     VARCHAR(40) NOT NULL,
  feature_name      VARCHAR(40) NOT NULL,
  raw_value         VARCHAR(80) NOT NULL,
  encoded_idx       INT         NOT NULL,
  is_unknown_bucket TINYINT(1)  NOT NULL DEFAULT 0,
  PRIMARY KEY (model_version, feature_name, raw_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 실제 발생 실적. ML 뷰는 COUNT(*) 조차 7분을 넘기므로 검증 전용 스냅샷을 둔다
CREATE TABLE IF NOT EXISTS ref_actual_daily (
  target_date DATE        NOT NULL,
  region_cd   VARCHAR(24) NOT NULL,
  fire_count  SMALLINT    NOT NULL DEFAULT 0,
  PRIMARY KEY (target_date, region_cd),
  KEY ix_region (region_cd, target_date),
  CONSTRAINT ck_actual_count CHECK (fire_count >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='사후 검증 기준 실적 — SCR-05';

CREATE TABLE IF NOT EXISTS ref_actual_spread (
  target_date DATE        NOT NULL,
  region_cd   VARCHAR(24) NOT NULL,
  fires       SMALLINT    NOT NULL DEFAULT 0,
  escalated   SMALLINT    NOT NULL DEFAULT 0,
  PRIMARY KEY (target_date, region_cd),
  CONSTRAINT ck_aspread_range CHECK (escalated >= 0 AND escalated <= fires)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='연소확대 실적 — SCR-05 확산 검증';

CREATE TABLE IF NOT EXISTS ref_actual_cause (
  target_date DATE        NOT NULL,
  region_cd   VARCHAR(24) NOT NULL,
  cause_class VARCHAR(40) NOT NULL,
  cnt         SMALLINT    NOT NULL DEFAULT 0,
  PRIMARY KEY (target_date, region_cd, cause_class),
  CONSTRAINT ck_acause_cnt CHECK (cnt >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='발화요인 실적 — SCR-05 원인 검증';


-- =============================================================================
-- 4) L3 예측 — 배치 산출물
--    FR-022: 재산출이 이전 예측을 덮어쓰지 않는다 → PK 에 run_id 포함,
--            현재본은 is_current = 1
-- =============================================================================

CREATE TABLE IF NOT EXISTS pred_daily (
  target_date          DATE          NOT NULL,
  region_cd            VARCHAR(24)   NOT NULL,
  run_id               BIGINT        NOT NULL,
  is_current           TINYINT(1)    NOT NULL DEFAULT 1        COMMENT '1=현재본, 0=이력',
  expected_count       DECIMAL(10,6) NULL                      COMMENT 'M1 기대 발생 건수',
  occur_probability    DECIMAL(7,6)  NULL                      COMMENT 'M5 발생 확률 (보정 후)',
  occur_prob_raw       DECIMAL(7,6)  NULL,
  risk_grade           ENUM('낮음','보통','높음','매우높음') NULL,
  risk_rank            SMALLINT      NULL,
  spread_probability   DECIMAL(7,6)  NULL                      COMMENT 'M2a — 미산출 시 NULL (0 금지)',
  longburn_probability DECIMAL(7,6)  NULL                      COMMENT 'M4 참고용 — 미산출 시 NULL',
  expected_damage_load DECIMAL(12,6) NULL                      COMMENT '기대 피해량 = 배분 수요 (FR-033)',
  top_factors          JSON          NULL                      COMMENT 'CS-07 기여 요인 문장 3개 이상',
  confidence           ENUM('정상','대리지점','폴백')   NOT NULL DEFAULT '정상',
  data_status          ENUM('정상','최신아님','데이터없음') NOT NULL DEFAULT '정상',
  source_model_version VARCHAR(40)   NULL,
  created_at           DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (target_date, region_cd, run_id),
  KEY ix_daily_rank (target_date, is_current, risk_rank),
  KEY ix_region_date (region_cd, target_date),
  KEY ix_current_grade (is_current, target_date, risk_grade),
  CONSTRAINT ck_pred_prob
    CHECK (occur_probability IS NULL OR (occur_probability >= 0 AND occur_probability <= 1)),
  CONSTRAINT ck_pred_spread
    CHECK (spread_probability IS NULL OR (spread_probability >= 0 AND spread_probability <= 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='시군구x일 위험도 예측 — 하루 251행 (SC-006)';

-- 입력 재구성 (SC-005). feature_grade 는 A 과거확정 / B 예보의존만.
-- C 사후한정이 들어오면 FR-040~043 위반이다 — schema_03_queries.sql R3 로 점검한다.
CREATE TABLE IF NOT EXISTS pred_feature_snapshot (
  target_date        DATE        NOT NULL,
  region_cd          VARCHAR(24) NOT NULL,
  run_id             BIGINT      NOT NULL,
  features           JSON        NULL,
  feature_grade      JSON        NULL                          COMMENT 'A/B 만 허용 · C 금지',
  forecast_issued_at DATETIME    NULL,
  station_id         VARCHAR(16) NULL,
  psi                JSON        NULL,
  PRIMARY KEY (target_date, region_cd, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='예측 입력 스냅샷 — SCR-02 "이 예측에 쓰인 값"';

-- 원인 후보. 화면은 순위를 쓰지 않고 lift_vs_base >= 2.0 만 노출한다 (BR-CAU-03)
CREATE TABLE IF NOT EXISTS pred_cause_top3 (
  target_date  DATE         NOT NULL,
  region_cd    VARCHAR(24)  NOT NULL,
  run_id       BIGINT       NOT NULL,
  `rank`       TINYINT      NOT NULL,
  cause_class  VARCHAR(40)  NOT NULL,
  probability  DECIMAL(7,6) NOT NULL,
  lift_vs_base DECIMAL(9,4) NULL                               COMMENT '평시 대비 배율',
  PRIMARY KEY (target_date, region_cd, run_id, `rank`),
  CONSTRAINT ck_cause_rank CHECK (`rank` BETWEEN 1 AND 3),
  CONSTRAINT ck_cause_prob CHECK (probability >= 0 AND probability <= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='발화 원인 후보 — SCR-02 이상 신호';


-- =============================================================================
-- 5) L4 운영 — 인력·제약·배분
-- =============================================================================

CREATE TABLE IF NOT EXISTS ops_deployment_status (
  region_cd  VARCHAR(24) NOT NULL,
  as_of      DATE        NOT NULL,
  on_duty    INT         NULL                                  COMMENT '현재 대기 인력',
  capacity   INT         NULL                                  COMMENT '정원 — 미확보 상태',
  min_retain INT         NULL                                  COMMENT '최소 잔류 — 미확보 상태',
  source     ENUM('담당자입력','시스템추정') NOT NULL DEFAULT '시스템추정',
  PRIMARY KEY (region_cd, as_of),
  CONSTRAINT ck_deploy_retain
    CHECK (min_retain IS NULL OR capacity IS NULL OR min_retain <= capacity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='지역별 인력 현황 — SCR-03 (미확보 시 비율 모드)';

CREATE TABLE IF NOT EXISTS ops_constraint (
  constraint_id   BIGINT AUTO_INCREMENT,
  region_cd       VARCHAR(24) NULL                             COMMENT 'NULL 이면 전역 제약',
  constraint_type ENUM('정원상한','최소잔류','관할이탈금지') NOT NULL,
  value           INT         NULL,
  is_active       TINYINT(1)  NOT NULL DEFAULT 1,
  PRIMARY KEY (constraint_id),
  KEY ix_region (region_cd, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='운영 제약 — SCR-04 저장 차단 근거 (FR-036)';

CREATE TABLE IF NOT EXISTS ops_allocation_proposal (
  proposal_id         BIGINT AUTO_INCREMENT,
  target_date         DATE          NOT NULL,
  run_id              BIGINT        NULL,
  mode                ENUM('ratio','absolute') NOT NULL DEFAULT 'ratio',
  policy              JSON          NULL                       COMMENT '{alpha,beta,delta} 산출에 쓴 정책값',
  unmet_risk_before   DECIMAL(16,6) NULL                       COMMENT '현재 배치 그대로',
  unmet_risk_after    DECIMAL(16,6) NULL                       COMMENT '권장안 적용 후',
  baseline_unmet_risk DECIMAL(16,6) NULL                       COMMENT '균등 배치 기준선',
  moved_total         DECIMAL(14,4) NULL                       COMMENT '권장안 실행 시 이동 인원',
  staff_source        VARCHAR(32)   NULL                       COMMENT '현재 인력 N 의 출처',
  created_at          DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (proposal_id),
  KEY ix_date (target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='배분안 헤더 — 같은 예측이어도 정책값이 다르면 답이 다르다';

CREATE TABLE IF NOT EXISTS ops_allocation_proposal_item (
  proposal_id           BIGINT        NOT NULL,
  region_cd             VARCHAR(24)   NOT NULL,
  demand_score          DECIMAL(14,6) NULL                     COMMENT '수요 = 기대 피해량',
  recommended_ratio     DECIMAL(10,8) NULL,
  need_headcount        DECIMAL(12,4) NULL                     COMMENT '위험 비례 목표 need(r)',
  current_staff         DECIMAL(12,4) NULL                     COMMENT '현재 인력 N(r)',
  recommended_headcount DECIMAL(12,4) NULL                     COMMENT '권장 배치 x(r)',
  rationale             JSON          NULL                     COMMENT 'FR-037 배정 근거',
  PRIMARY KEY (proposal_id, region_cd),
  CONSTRAINT ck_item_ratio
    CHECK (recommended_ratio IS NULL OR (recommended_ratio >= 0 AND recommended_ratio <= 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 사유 없는 조정은 이력이 될 수 없다 (FR-039 · SC-009)
CREATE TABLE IF NOT EXISTS ops_allocation_adjustment (
  adjustment_id        BIGINT AUTO_INCREMENT,
  proposal_id          BIGINT      NOT NULL,
  author_id            VARCHAR(80) NOT NULL,
  author_role          ENUM('상황실','지휘','관리자','정책') NOT NULL,
  created_at           DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  reason               TEXT        NOT NULL,
  constraint_violation JSON        NULL,
  PRIMARY KEY (adjustment_id),
  KEY ix_proposal (proposal_id),
  CONSTRAINT ck_adj_reason CHECK (CHAR_LENGTH(reason) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='배분 조정 헤더 — SCR-04';

CREATE TABLE IF NOT EXISTS ops_allocation_adjustment_item (
  adjustment_id         BIGINT        NOT NULL,
  region_cd             VARCHAR(24)   NOT NULL,
  recommended_headcount INT           NULL,
  adjusted_headcount    INT           NULL,
  adjusted_ratio        DECIMAL(10,8) NULL,
  PRIMARY KEY (adjustment_id, region_cd),
  CONSTRAINT ck_adjitem_nonneg
    CHECK (adjusted_headcount IS NULL OR adjusted_headcount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- 6) L4 신규 — 화면 설계에서 도출 (UI_02 SCR-08 · SCR-09)
--    현재 스키마로 채울 수 없다고 확인된 칸만 만든다.
-- =============================================================================

-- SCR-08. 게이트 G18~G21 의 판정 결과를 화면이 보여줄 수 있게 한다.
-- 지금은 holdout_metrics JSON 을 사람이 해석해야 "왜 참고용인지" 알 수 있다.
CREATE TABLE IF NOT EXISTS ops_model_acceptance (
  model_version  VARCHAR(40)   NOT NULL,
  criterion_code VARCHAR(40)   NOT NULL                        COMMENT '예: recall_at_10',
  gate_code      ENUM('G18','G19','G20','G21') NULL,
  criterion_desc VARCHAR(200)  NOT NULL                        COMMENT '예: 공식 대형화재 recall@10%',
  threshold_expr VARCHAR(80)   NOT NULL                        COMMENT "예: '> 0.50'",
  observed_value DECIMAL(18,6) NULL,
  verdict        ENUM('PASS','FAIL') NOT NULL,
  sort_order     SMALLINT      NOT NULL DEFAULT 0,
  evaluated_at   DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (model_version, criterion_code),
  KEY ix_acceptance_verdict (verdict, model_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='모델 합격 기준 판정표 — SCR-08';

-- SCR-09. 실행이 어느 단계에서 멈췄는지 저장할 곳이 지금은 없다.
-- 단계 5종은 A1 액티비티 다이어그램의 파티션과 1:1 대응한다.
CREATE TABLE IF NOT EXISTS ops_batch_step (
  run_id      BIGINT      NOT NULL,
  step_no     TINYINT     NOT NULL,
  step_code   ENUM('개시판정','선행조건','기상확보','추론','적재마감') NOT NULL,
  status      ENUM('성공','실패','건너뜀','실행중') NOT NULL,
  started_at  DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  detail      JSON        NULL,
  PRIMARY KEY (run_id, step_no),
  CONSTRAINT ck_step_order CHECK (step_no BETWEEN 1 AND 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='배치 실행 단계 타임라인 — SCR-09';

-- SCR-09. failure_detail JSON 으로는 "이 지역이 몇 번 실패했나" 를 집계할 수 없다.
CREATE TABLE IF NOT EXISTS ops_batch_region_failure (
  run_id      BIGINT       NOT NULL,
  region_cd   VARCHAR(24)  NOT NULL,
  stage       VARCHAR(30)  NOT NULL                            COMMENT '실패 단계',
  reason      VARCHAR(300) NOT NULL,
  occurred_at DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (run_id, region_cd),
  KEY ix_bfail_region (region_cd, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='지역별 배치 실패 사유 (정규화) — SCR-07 · SCR-09';

-- SCR-07 · SCR-09. 산출 모듈이 아직 없다.
-- 테이블이 비어 있는 것과 '이상 없음' 은 다르다 — 화면은 '미측정' 으로 표기한다.
CREATE TABLE IF NOT EXISTS ops_feature_drift (
  run_id       BIGINT        NOT NULL,
  feature_name VARCHAR(40)   NOT NULL,
  psi          DECIMAL(10,6) NOT NULL,
  threshold    DECIMAL(10,6) NOT NULL DEFAULT 0.25,
  alert_flag   TINYINT(1)    NOT NULL DEFAULT 0,
  measured_at  DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (run_id, feature_name),
  KEY ix_drift_alert (alert_flag, run_id),
  CONSTRAINT ck_drift_psi CHECK (psi >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='입력 분포 변화(PSI) — 현재 미측정';

-- =============================================================================
-- 끝. 생성 확인은 schema_03_queries.sql §0 을 실행한다.
-- =============================================================================
