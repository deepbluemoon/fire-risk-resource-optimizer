-- 운영 계층 (data-model.md §3)
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS ops_model_registry (
  model_version     VARCHAR(40) NOT NULL,
  model_id          ENUM('M0','M1','M5','M2a','M3','M4') NOT NULL,
  algorithm         VARCHAR(60) NULL,
  artifact_path     VARCHAR(255) NULL,
  train_data_range  JSON NULL,
  feature_list      JSON NULL,
  cv_metrics        JSON NULL,
  holdout_metrics   JSON NULL,
  acceptance_passed TINYINT(1) NOT NULL DEFAULT 0,
  code_commit       VARCHAR(40) NULL,
  is_active         TINYINT(1) NOT NULL DEFAULT 0,
  created_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (model_version),
  KEY ix_active (model_id, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ops_batch_run (
  run_id             BIGINT AUTO_INCREMENT,
  target_date        DATE NOT NULL,
  started_at         DATETIME(3) NOT NULL,
  finished_at        DATETIME(3) NULL,
  status             ENUM('실행중','성공','부분실패','실패','폴백') NOT NULL DEFAULT '실행중',
  regions_ok         SMALLINT NOT NULL DEFAULT 0,
  regions_failed     SMALLINT NOT NULL DEFAULT 0,
  failure_detail     JSON NULL,
  forecast_fetched_at DATETIME NULL,
  fallback_source    ENUM('전일결과','M0기저율') NULL,
  drift_alerts       JSON NULL,
  PRIMARY KEY (run_id),
  KEY ix_target (target_date, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ops_deployment_status (
  region_cd  VARCHAR(24) NOT NULL,
  as_of      DATE NOT NULL,
  on_duty    INT NULL,
  capacity   INT NULL,
  min_retain INT NULL,
  source     ENUM('담당자입력','시스템추정') NOT NULL DEFAULT '시스템추정',
  PRIMARY KEY (region_cd, as_of)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ops_constraint (
  constraint_id   BIGINT AUTO_INCREMENT,
  region_cd       VARCHAR(24) NULL,
  constraint_type ENUM('정원상한','최소잔류','관할이탈금지') NOT NULL,
  value           INT NULL,
  is_active       TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (constraint_id),
  KEY ix_region (region_cd, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ops_allocation_proposal (
  proposal_id        BIGINT AUTO_INCREMENT,
  target_date        DATE NOT NULL,
  run_id             BIGINT NULL,
  mode               ENUM('ratio','absolute') NOT NULL DEFAULT 'ratio',
  unmet_risk_before  DECIMAL(16,6) NULL,
  unmet_risk_after   DECIMAL(16,6) NULL,
  baseline_unmet_risk DECIMAL(16,6) NULL,
  created_at         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (proposal_id),
  KEY ix_date (target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ops_allocation_proposal_item (
  proposal_id          BIGINT NOT NULL,
  region_cd            VARCHAR(24) NOT NULL,
  demand_score         DECIMAL(14,6) NULL,
  recommended_ratio    DECIMAL(10,8) NULL,
  recommended_headcount INT NULL,
  rationale            JSON NULL,
  PRIMARY KEY (proposal_id, region_cd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ops_allocation_adjustment (
  adjustment_id BIGINT AUTO_INCREMENT,
  proposal_id   BIGINT NOT NULL,
  author_id     VARCHAR(80) NOT NULL,
  author_role   ENUM('상황실','지휘','관리자','정책') NOT NULL,
  created_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  reason        TEXT NOT NULL,
  constraint_violation JSON NULL,
  PRIMARY KEY (adjustment_id),
  KEY ix_proposal (proposal_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ops_allocation_adjustment_item (
  adjustment_id        BIGINT NOT NULL,
  region_cd            VARCHAR(24) NOT NULL,
  recommended_headcount INT NULL,
  adjusted_headcount   INT NULL,
  adjusted_ratio       DECIMAL(10,8) NULL,
  PRIMARY KEY (adjustment_id, region_cd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
