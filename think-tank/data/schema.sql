-- ============================================================
-- Populism & Crisis Early Warning Research Database
-- populist_crisis.db
-- ============================================================
-- 设计原则：
-- 1. 三层数据对齐（事件层/社媒层/官方源层）→ 统一时间轴
-- 2. 原始数据保留出处，不修改；派生指标另存
-- 3. 支持多语、多平台、多数据源交叉查询
-- 4. 合规优先：原文最小化保存，以聚合/指标为主
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ============================================================
-- 1. 元数据与版本控制
-- ============================================================
CREATE TABLE IF NOT EXISTS data_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,          -- 'ACLED' / 'GDELT_EVENTS' / 'GDELT_GKG' / 'UCDP' / 'REDDIT' / 'X_TWITTER' / 'OFFICIAL'
    version       TEXT NOT NULL,          -- 语义版本或日期标签
    download_date TEXT NOT NULL,          -- YYYY-MM-DD
    query_params  TEXT,                   -- JSON: 查询参数（地区/时间/筛选条件）
    record_count  INTEGER,
    notes         TEXT
);
CREATE UNIQUE INDEX idx_dv_source_ver ON data_versions(source, version);

-- ============================================================
-- 2. 地理与政治实体（统一编码）
-- ============================================================
CREATE TABLE IF NOT EXISTS locations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    country       TEXT NOT NULL,
    country_code  TEXT,                   -- ISO 3166-1 alpha-2
    adm_1         TEXT,                   -- 省/州
    adm_2         TEXT,                   -- 市/县
    latitude      REAL,
    longitude     REAL,
    region        TEXT,                   -- 大区：'Eastern Europe' / 'Middle East' 等
    UNIQUE(country, adm_1, adm_2)
);

-- ============================================================
-- 3. 政治行为体
-- ============================================================
CREATE TABLE IF NOT EXISTS actors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    name_en       TEXT,
    actor_type    TEXT,                   -- 'state_forces' / 'rebel_group' / 'political_party' / 'social_movement' / 'media' / 'individual'
    side          TEXT,                   -- 'side_a' / 'side_b' / 'other'
    source_db     TEXT,                   -- 来自哪个数据库的编码
    source_id     TEXT,                   -- 原始数据库中的ID
    notes         TEXT,
    UNIQUE(source_db, source_id)
);

-- ============================================================
-- 4. 事件层 - 主表（ACLED + UCDP 合并视图的基础）
-- ============================================================
CREATE TABLE IF NOT EXISTS conflict_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 统一标识
    source        TEXT NOT NULL,          -- 'ACLED' / 'UCDP'
    source_id     TEXT NOT NULL,          -- 原始事件ID
    -- 时间
    event_date    TEXT NOT NULL,          -- YYYY-MM-DD
    date_start    TEXT,                   -- UCDP: 开始日期
    date_end      TEXT,                   -- UCDP: 结束日期
    date_prec     INTEGER DEFAULT 1,      -- 1=精确日 2=月 3=年
    -- 空间
    location_id   INTEGER REFERENCES locations(id),
    country       TEXT NOT NULL,
    latitude      REAL,
    longitude     REAL,
    where_desc    TEXT,                   -- 地点描述
    -- 行为体
    actor_a_id    INTEGER REFERENCES actors(id),
    actor_b_id    INTEGER REFERENCES actors(id),
    actor_a       TEXT,                   -- 冗余保留原文
    actor_b       TEXT,
    interaction   TEXT,                   -- 交互类型编码
    -- 事件属性
    event_type    TEXT,                   -- 'Battles' / 'Explosions' / 'Protests' / 'Riots' / 'Violence against civilians'
    sub_event_type TEXT,
    fatalities    INTEGER DEFAULT 0,
    fatalities_a  INTEGER DEFAULT 0,
    fatalities_b  INTEGER DEFAULT 0,
    fatalities_civilians INTEGER DEFAULT 0,
    -- 数据来源
    source_article TEXT,                  -- 原始新闻来源URL/标题
    source_date   TEXT,
    notes         TEXT,
    -- 索引与元数据
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(source, source_id)
);
CREATE INDEX idx_ce_date ON conflict_events(event_date);
CREATE INDEX idx_ce_country ON conflict_events(country);
CREATE INDEX idx_ce_type ON conflict_events(event_type);
CREATE INDEX idx_ce_latlon ON conflict_events(latitude, longitude);
CREATE INDEX idx_ce_fatalities ON conflict_events(fatalities);

-- ============================================================
-- 5. GDELT 事件数据库（高维：数百万条）
-- ============================================================
CREATE TABLE IF NOT EXISTS gdelt_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    global_event_id TEXT NOT NULL,
    event_date    TEXT NOT NULL,          -- YYYYMMDD
    -- 参与者
    actor1_code   TEXT,
    actor1_name   TEXT,
    actor2_code   TEXT,
    actor2_name   TEXT,
    -- 事件编码 (CAMEO)
    event_code    TEXT,                   -- 如 '0141'
    event_base_code TEXT,
    root_code     TEXT,
    -- 地理
    action_geo_type INTEGER,
    action_geo_fullname TEXT,
    action_geo_country_code TEXT,
    action_geo_lat REAL,
    action_geo_lon REAL,
    -- 事件属性
    goldstein_scale REAL,                 -- -10 到 +10
    num_mentions  INTEGER,
    num_sources   INTEGER,
    num_articles  INTEGER,
    avg_tone      REAL,                   -- GDELT tone（-20 到 +20）
    -- 数据管理
    source_file   TEXT,                   -- 来源文件名
    version_id    INTEGER REFERENCES data_versions(id),
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(global_event_id)
);
CREATE INDEX idx_ge_date ON gdelt_events(event_date);
CREATE INDEX idx_ge_country ON gdelt_events(action_geo_country_code);
CREATE INDEX idx_ge_code ON gdelt_events(event_code);
CREATE INDEX idx_ge_tone ON gdelt_events(avg_tone);
CREATE INDEX idx_ge_goldstein ON gdelt_events(goldstein_scale);

-- ============================================================
-- 6. GDELT GKG（主题/实体/语调）
-- ============================================================
CREATE TABLE IF NOT EXISTS gdelt_gkg (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    gkg_record_id TEXT NOT NULL,
    date          TEXT NOT NULL,          -- YYYYMMDDHHMMSS
    -- 来源
    source_url    TEXT,
    source_domain TEXT,
    -- 主题与实体
    themes        TEXT,                   -- 逗号分隔主题列表
    locations     TEXT,                   -- 逗号分隔地理位置
    persons       TEXT,                   -- 提及人物
    organizations TEXT,
    tone          REAL,                   -- GKG tone
    -- 数量
    num_articles  INTEGER DEFAULT 1,
    -- 内容（可选，合规审计后决定是否保存）
    all_names     TEXT,
    amounts       TEXT,
    -- 数据管理
    source_file   TEXT,
    version_id    INTEGER REFERENCES data_versions(id),
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(gkg_record_id)
);
CREATE INDEX idx_gkg_date ON gdelt_gkg(date);
CREATE INDEX idx_gkg_tone ON gdelt_gkg(tone);
CREATE INDEX idx_gkg_domain ON gdelt_gkg(source_domain);

-- ============================================================
-- 7. 社媒层 - 帖子（Reddit / X / TikTok）
-- ============================================================
CREATE TABLE IF NOT EXISTS social_posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,          -- 'reddit' / 'x_twitter' / 'tiktok'
    post_id       TEXT NOT NULL,          -- 平台原始ID
    parent_id     TEXT,                   -- 父帖子/回复目标ID
    author_id     TEXT,                   -- 平台用户ID（哈希后存储）
    subreddit     TEXT,                   -- Reddit: 子版块
    created_utc   TEXT,                   -- UTC时间戳
    title         TEXT,
    body_text     TEXT,                   -- 帖子正文（合规处理后）
    language      TEXT,                   -- 检测到的语言
    -- 互动数据
    score         INTEGER DEFAULT 0,      -- Reddit: upvotes; X: likes
    num_comments  INTEGER DEFAULT 0,
    shares_retweets INTEGER DEFAULT 0,
    -- 元数据
    source_url    TEXT,
    is_deleted    INTEGER DEFAULT 0,      -- 合规标记：是否已删除
    compliance_checked_at TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(platform, post_id)
);
CREATE INDEX idx_sp_platform_date ON social_posts(platform, created_utc);
CREATE INDEX idx_sp_subreddit ON social_posts(subreddit);
CREATE INDEX idx_sp_language ON social_posts(language);

-- ============================================================
-- 8. 文本分析指标（每条帖子的派生指标）
-- ============================================================
CREATE TABLE IF NOT EXISTS text_indicators (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       INTEGER NOT NULL REFERENCES social_posts(id),
    -- 民粹成分 (P)
    populism_strength REAL,               -- 0-100 综合民粹强度
    ppl_density   REAL,                   -- 人民指向词密度
    elite_density REAL,                   -- 精英指向词密度
    outgroup_density REAL,                -- 外群指向词密度
    binary_score  REAL,                   -- 二元对立同时出现加分
    -- 道德化 (M)
    moralization_strength REAL,           -- 道德化语言强度
    -- 情绪 (N)
    negativity    REAL,                   -- 负性情绪强度
    arousal       REAL,                   -- 唤醒度
    -- 威胁/对抗 (H)
    hostility     REAL,                   -- 威胁化/对抗强度
    -- 综合
    discourse_temp REAL,                  -- 话语温度 = αP + βM + γN + δH
    temp_weight_a REAL,                   -- 权重α（民粹）
    temp_weight_b REAL,                   -- 权重β（道德化）
    temp_weight_g REAL,                   -- 权重γ（负性）
    temp_weight_d REAL,                   -- 权重δ（威胁）
    -- 词表版本
    lexicon_version TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(post_id)
);
CREATE INDEX idx_ti_temp ON text_indicators(discourse_temp);
CREATE INDEX idx_ti_populism ON text_indicators(populism_strength);

-- ============================================================
-- 9. 社交网络（用于极化/扩散分析）
-- ============================================================
CREATE TABLE IF NOT EXISTS social_edges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_author_id TEXT NOT NULL,
    target_author_id TEXT NOT NULL,
    platform      TEXT NOT NULL,
    edge_type     TEXT,                   -- 'reply' / 'retweet' / 'mention' / 'share'
    weight        INTEGER DEFAULT 1,      -- 互动次数
    first_seen    TEXT,
    last_seen     TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(platform, source_author_id, target_author_id, edge_type)
);
CREATE INDEX idx_se_source ON social_edges(source_author_id);
CREATE INDEX idx_se_target ON social_edges(target_author_id);

-- ============================================================
-- 10. 官方事件源（联合国/欧盟/北约/欧安组织）
-- ============================================================
CREATE TABLE IF NOT EXISTS official_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_org    TEXT NOT NULL,          -- 'un' / 'eu_council' / 'nato' / 'osce'
    event_type    TEXT,                   -- 'resolution' / 'statement' / 'sanctions' / 'meeting'
    event_date    TEXT NOT NULL,
    title         TEXT,
    summary       TEXT,
    source_url    TEXT NOT NULL,
    document_ref  TEXT,                   -- 文件编号
    countries_mentioned TEXT,             -- 逗号分隔
    tags          TEXT,                   -- 'sanctions_extension' / 'military_aid' 等
    -- 预警标注
    is_crisis_node INTEGER DEFAULT 0,     -- 是否为危机升级节点
    crisis_level  TEXT,                   -- 'low' / 'medium' / 'high' / 'critical'
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(source_url)
);
CREATE INDEX idx_oe_date ON official_events(event_date);
CREATE INDEX idx_oe_org ON official_events(source_org);

-- ============================================================
-- 11. 预警状态（分级状态机）
-- ============================================================
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_date    TEXT NOT NULL,
    alert_level   TEXT NOT NULL,          -- 'green' / 'yellow' / 'orange' / 'red'
    trigger_type  TEXT NOT NULL,          -- 'temp_rise' / 'platform_divergence' / 'algo_amplification' / 'event_resonance' / 'media_anomaly'
    -- 触发指标
    trigger_details TEXT,                 -- JSON: 具体哪个指标触发、阈值、当前值
    -- 聚合窗口
    window_start  TEXT,
    window_end    TEXT,
    aggregation   TEXT,                   -- 'daily' / 'weekly'
    -- 地域/议题
    region        TEXT,
    topic         TEXT,
    -- 解释
    explanation   TEXT,                   -- 人类可读的触发原因
    recommendation TEXT,                  -- 建议响应
    -- 验证
    resolved      INTEGER DEFAULT 0,      -- 是否已验证/解决
    actual_event_id INTEGER REFERENCES conflict_events(id),  -- 关联的实际事件
    lead_time_days REAL,                  -- 提前天数（事后填写）
    is_false_alarm INTEGER DEFAULT 0,     -- 是否误报
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_al_date ON alerts(alert_date);
CREATE INDEX idx_al_level ON alerts(alert_level);

-- ============================================================
-- 12. 时间序列聚合（日/周级别的温度序列）
-- ============================================================
CREATE TABLE IF NOT EXISTS time_series (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_type   TEXT NOT NULL,          -- 'discourse_temp' / 'populism' / 'moralization' / 'negativity' / 'hostility' / 'event_intensity' / 'gdelt_tone'
    granularity   TEXT NOT NULL,          -- 'daily' / 'weekly'
    period_start  TEXT NOT NULL,          -- YYYY-MM-DD
    platform      TEXT,                   -- NULL=全平台
    country       TEXT,
    language      TEXT,
    value         REAL NOT NULL,          -- 聚合值
    value_p25     REAL,
    value_p50     REAL,
    value_p75     REAL,
    value_p95     REAL,
    value_mean    REAL,
    value_std     REAL,
    count         INTEGER,                -- 该窗口内的帖子/事件数
    weight_method TEXT,                   -- 'equal' / 'engagement' / 'exposure_proxy'
    lexicon_version TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(series_type, granularity, period_start, platform, country, language)
);
CREATE INDEX idx_ts_type_date ON time_series(series_type, period_start);

-- ============================================================
-- 13. 反事实仿真结果
-- ============================================================
CREATE TABLE IF NOT EXISTS simulations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_name      TEXT NOT NULL,          -- 'chronological' / 'engagement_ranked' / 'homophily_boosted'
    description   TEXT,
    -- 参数
    params        TEXT,                   -- JSON: 仿真参数
    -- 结果
    result        TEXT,                   -- JSON: 暴露温度分布、扩散规模、极化变化
    -- 对比
    baseline_sim_id INTEGER REFERENCES simulations(id),  -- 基准仿真ID
    amplification_ratio REAL,             -- 算法放大比 AR
    -- 元数据
    window_start  TEXT,
    window_end    TEXT,
    platform      TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 14. 民粹词表（多语、版本控制）
-- ============================================================
CREATE TABLE IF NOT EXISTS populism_lexicon (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    version       TEXT NOT NULL,
    language      TEXT NOT NULL,          -- 'en' / 'ru' / 'uk' / 'pl' / 'hu' / 'ro' / 'cs' / 'sk'
    concept_layer TEXT NOT NULL,          -- 'people' / 'elite' / 'outgroup' / 'sovereignty_victimhood' / 'moral_mobilization'
    word          TEXT NOT NULL,
    word_type     TEXT,                   -- 'seed' / 'morphological_extension' / 'pmi_expansion' / 'crosslingual_neighbor'
    -- 证据
    evidence_type TEXT,                   -- 'manual' / 'pmi' / 'crosslingual_embedding'
    evidence_score REAL,
    example_sentence TEXT,
    target_ref    TEXT,                   -- 指向对象：'elite' / 'outgroup' / 'intl_org' / 'media'
    -- 版本管理
    added_in      TEXT,                   -- 该词在哪个版本添加
    add_reason    TEXT,
    active        INTEGER DEFAULT 1,      -- 是否在当前版本中启用
    UNIQUE(version, language, word)
);
CREATE INDEX idx_lex_ver ON populism_lexicon(version, language);
CREATE INDEX idx_lex_concept ON populism_lexicon(concept_layer, language);

-- ============================================================
-- 15. 预警规则与阈值
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name     TEXT NOT NULL,
    trigger_type  TEXT NOT NULL,          -- 与 alerts.trigger_type 对应
    condition     TEXT NOT NULL,          -- SQL表达式或伪代码描述
    threshold     REAL,
    threshold_unit TEXT,                  -- 'percentile' / 'absolute' / 'ratio'
    baseline_type TEXT,                   -- 'rolling_30d' / 'rolling_90d' / 'fixed'
    priority      TEXT NOT NULL,          -- 'low' / 'medium' / 'high' / 'critical'
    response      TEXT,                   -- 建议响应动作
    active        INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 视图：统一时间轴
-- ============================================================
CREATE VIEW IF NOT EXISTS unified_timeline AS
SELECT 
    event_date AS date,
    'conflict' AS layer,
    source,
    country,
    event_type AS category,
    fatalities AS intensity,
    source_id AS ref_id,
    NULL AS platform,
    NULL AS language
FROM conflict_events
UNION ALL
SELECT 
    substr(date, 1, 4) || '-' || substr(date, 5, 2) || '-' || substr(date, 7, 2) AS date,
    'gdelt_event' AS layer,
    'GDELT' AS source,
    action_geo_country_code AS country,
    event_code AS category,
    goldstein_scale AS intensity,
    global_event_id AS ref_id,
    NULL AS platform,
    NULL AS language
FROM gdelt_events
UNION ALL
SELECT 
    created_utc AS date,
    'social' AS layer,
    platform AS source,
    NULL AS country,
    NULL AS category,
    NULL AS intensity,
    post_id AS ref_id,
    platform,
    language
FROM social_posts
UNION ALL
SELECT 
    event_date AS date,
    'official' AS layer,
    source_org AS source,
    NULL AS country,
    event_type AS category,
    NULL AS intensity,
    source_url AS ref_id,
    NULL AS platform,
    NULL AS language
FROM official_events
WHERE is_crisis_node = 1;

-- ============================================================
-- 视图：每日话语温度汇总
-- ============================================================
CREATE VIEW IF NOT EXISTS daily_temperature AS
SELECT 
    substr(sp.created_utc, 1, 10) AS date,
    sp.platform,
    sp.language,
    AVG(ti.discourse_temp) AS avg_temp,
    AVG(ti.populism_strength) AS avg_populism,
    AVG(ti.moralization_strength) AS avg_moral,
    AVG(ti.negativity) AS avg_neg,
    AVG(ti.hostility) AS avg_hostile,
    COUNT(*) AS post_count,
    SUM(CASE WHEN ti.discourse_temp > 70 THEN 1 ELSE 0 END) AS high_temp_count
FROM social_posts sp
JOIN text_indicators ti ON sp.id = ti.post_id
GROUP BY substr(sp.created_utc, 1, 10), sp.platform, sp.language;
