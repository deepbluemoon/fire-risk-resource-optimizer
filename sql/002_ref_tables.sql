-- 참조 테이블 (data-model.md §1) — 배치가 WRITE, API 가 READ
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS ref_region_profile (
  region_cd            VARCHAR(24)  NOT NULL,
  sido                 VARCHAR(30)  NOT NULL,
  sigungu              VARCHAR(30)  NOT NULL,
  area_km2             DECIMAL(12,3) NULL,
  forest_ratio         DECIMAL(8,4)  NULL,
  timber_volume        DECIMAL(14,2) NULL,
  population           INT           NULL,
  households           INT           NULL,
  pop_density          DECIMAL(12,2) NULL,
  log_population       DECIMAL(10,6) NULL,
  avg_station_distance_km DECIMAL(8,2) NULL,
  PRIMARY KEY (region_cd),
  KEY ix_sido (sido)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ref_region_cluster (
  region_cd     VARCHAR(24) NOT NULL,
  model_version VARCHAR(40) NOT NULL,
  cluster_id    TINYINT     NOT NULL,
  cluster_label VARCHAR(40) NULL,
  PRIMARY KEY (region_cd, model_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ref_station_map (
  region_cd     VARCHAR(24) NOT NULL,
  station_id    VARCHAR(16) NULL,
  assign_method ENUM('관내','시도평균','산악','도서') NOT NULL DEFAULT '관내',
  grid_nx       SMALLINT NULL,
  grid_ny       SMALLINT NULL,
  is_proxy      TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (region_cd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ref_risk_grade (
  model_version  VARCHAR(40) NOT NULL,
  grade          ENUM('낮음','보통','높음','매우높음') NOT NULL,
  prob_lower     DECIMAL(7,6) NOT NULL,
  prob_upper     DECIMAL(7,6) NOT NULL,
  sort_order     TINYINT NOT NULL,
  color_token    VARCHAR(32) NULL,
  description_ko VARCHAR(300) NULL,
  PRIMARY KEY (model_version, grade)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ref_category_map (
  model_version    VARCHAR(40) NOT NULL,
  feature_name     VARCHAR(40) NOT NULL,
  raw_value        VARCHAR(80) NOT NULL,
  encoded_idx      INT NOT NULL,
  is_unknown_bucket TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (model_version, feature_name, raw_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 실제 발생 실적 (사후 검증용). v_ml_occurrence 는 CROSS JOIN + 윈도우 함수로
-- COUNT(*) 조차 7분을 넘기므로, 검증 조회 전용 스냅샷 테이블을 따로 둔다.
CREATE TABLE IF NOT EXISTS ref_actual_daily (
  target_date DATE        NOT NULL,
  region_cd   VARCHAR(24) NOT NULL,
  fire_count  SMALLINT    NOT NULL DEFAULT 0,
  PRIMARY KEY (target_date, region_cd),
  KEY ix_region (region_cd, target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 확산·원인 사후 검증용 실적 (US5 시나리오 3)
CREATE TABLE IF NOT EXISTS ref_actual_spread (
  target_date DATE        NOT NULL,
  region_cd   VARCHAR(24) NOT NULL,
  fires       SMALLINT    NOT NULL DEFAULT 0,
  escalated   SMALLINT    NOT NULL DEFAULT 0,
  PRIMARY KEY (target_date, region_cd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ref_actual_cause (
  target_date DATE        NOT NULL,
  region_cd   VARCHAR(24) NOT NULL,
  cause_class VARCHAR(40) NOT NULL,
  cnt         SMALLINT    NOT NULL DEFAULT 0,
  PRIMARY KEY (target_date, region_cd, cause_class)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
