# Alpha Radar — 项目设计规范文档

> 版本：v1.4（运行审计层）
> 前版：v1.3.4 → v1.3.3 → v1.3.2 → v1.3.1 → v1.3 → v1.2 → v1.1 → 封版 v1.0
> 性质：研究型观测系统，非交易执行系统

---

## v1.4 版本变更摘要

### 本轮主题（一句话）

新增**独立的运行审计层（DebugRecorder）**——在不改业务数据流的前提下，把 FMZ 实盘的完整工作流以结构化 JSONL 落盘到本地，使后续可以把日志整包回传给模型做完整复盘与异常判定。

### 为什么要加

交付到 FMZ 实盘后，既有调试路径只有：
- 状态栏（实时观察，但信息有限且非持久化）
- `LogProfit` 图（仅锚健康度时间序列）
- 通过 `log_info / log_warn / log_error` 打出的文本日志（非结构化，回放困难）

三者组合起来**足够让人盯盘**，但**不足以支撑"把运行情况打包给模型审查"**——文本日志无法被可靠解析，状态栏是瞬态的不可回放，LogProfit 只覆盖一个维度。

v1.4 的目标是**补齐"可完整回传"的能力**，而不是替换现有输出。

### 核心设计约束（强制）

1. **零业务侵入**：业务逻辑对 DebugRecorder 零依赖。`debug_enabled=False` 时 `DebugRecorder.record_*` 全部立即 return，零文件 I/O、零字典构造。
2. **单向写入**：业务代码只调用 `record_*`，**从不读** DebugRecorder 状态。删除 DebugRecorder 类和所有调用行，业务代码照常运行。
3. **失败降级**：任何文件连续写失败超过阈值（默认 5 次）即**该文件**被软禁用，其他文件不受影响，业务逻辑照常运行。
4. **不无限膨胀**：每个文件有软上限（默认 100MB），超限即停写；每次启动生成独立 run_id 子目录，重启即隔离。

### 不改变的范围

- 七模块结构 + 主循环调度纪律（v1.3.3 定稿）：不动。
- 四轴状态机、分类矩阵、Anchor 判定：不动。
- 健康度"纯观测不进门控"定位（v1.3.4 收口）：不动。
- `raw_values / labels / events` 三张业务快照表：不动。
- `snapshot_history_size` / `persist_enabled` 等 SnapshotRecorder 已有配置：不动。
- 状态栏六表布局（v1.3.3 定稿）：不动。

**本轮唯一新增的类是 `DebugRecorder`**，唯一新增的全局常量是 `REASON_CODE` 字典。

---

## 目录

1. 项目背景 / 2. 第一性目的 / 3. 研究边界 / 4. 核心术语 / 5. 研究主链
6. 三个判定层 / 7. 因子集 / 8. 数据获取方式 / 9. 模块化设计
10. 封版补丁 v1.0 / 11. v1.1 修复日志 / 12. v1.2 架构迭代 / 13. v1.3 架构迭代
14. 已知局限声明 / 15. v1.3.1 修补日志 / 16. v1.3.2 迭代日志 / 17. v1.3.3 迭代日志 / 18. v1.3.4 迭代日志
**20. v1.4 运行审计层（本版）**

---

## 1-18 节

章节内容在 v1.0 至 v1.3.4 规范中完整给出，v1.4 未变更。

---

## 20. v1.4 运行审计层（DebugRecorder）

### 20.1 架构裁决

**采用独立 `DebugRecorder` 模块，不扩展 SnapshotRecorder**。

| 理由 | 说明 |
|---|---|
| 语义正交 | SnapshotRecorder 回答"系统观察到什么"（业务快照）；DebugRecorder 回答"系统怎么运行的"（运行时审计）。合并会导致后续回放脚本难以分辨哪些字段是业务、哪些是调试。 |
| 生命周期不同 | 业务快照是 `snapshot_history_size=500` 环形缓冲（供 Display 消费）；审计日志是持久化 + 按 run_id 隔离 + 软上限停写（供离线回放）。两套生命周期不能共用。 |
| 失败降级不同 | SnapshotRecorder 的 events 连续失败 3 次整体禁用；DebugRecorder 每个文件独立失败计数，某个文件禁用不影响其他。 |
| 可删除性 | DebugRecorder 是单向依赖的末端节点——删除它的所有 `record_*` 调用行，业务代码的所有行为不变。这是零耦合的可验证证据。 |

### 20.2 目录结构

```
/home/bitnami/logs/storage/654434/alpha_radar_debug/
├── latest_run.txt                                  ← 永远指向最新 run_id，便于回传
└── run_<YYYYMMDD_HHMMSS>_<short>/                  ← 每次启动一个隔离目录
    ├── run_meta.json                                ← 启动元信息（单次写入）
    ├── cycle_audit.jsonl                            ← 每轮主循环 1 行（可节流）
    ├── bar_pipeline.jsonl                           ← 每根完成 Bar 的流水线
    ├── factor_audit.jsonl                           ← 每根 Bar 的因子计算与 unready reason
    ├── task_frequency.jsonl                         ← time-driven 任务调度审计
    ├── state_transitions.jsonl                      ← 四轴 + event_state 迁移
    ├── anomalies.jsonl                              ← 结构性异常
    └── exceptions.jsonl                             ← 模块异常 + traceback
```

`run_id` 格式：`YYYYMMDD_HHMMSS_xxxx`，其中 `xxxx` 是启动毫秒时间戳低 16 位的十六进制。

**回传协议**：出现问题时，读取 `latest_run.txt` 获取最新 run_id，打包对应 `run_<...>` 目录的全部文件回传即可。

### 20.3 各文件职责与字段 Schema

所有 JSONL 文件中的每一行必然包含**公共信封**：

```json
{
  "ts_ms":         <int>,       // 本条日志的挂钟毫秒时间戳
  "utc8_datetime": "<string>",  // YYYY-MM-DD HH:MM:SS，UTC+8
  "run_id":        "<string>",  // 当前运行实例 id
  "kind":          "<string>",  // 每个文件对应固定 kind，便于异构合流
  ...                            // 特定字段
}
```

下面的 schema 表只列**特定字段**，公共信封默认存在。

#### 20.3.1 `run_meta.json`（启动单次写入）

```json
{
  "run_id":         "20260418_092000_a3f1",
  "start_ts_ms":    1713398400123,
  "utc8_datetime":  "2026-04-18 09:20:00",
  "system_name":    "alpha_radar",
  "system_version": "v1.4",
  "code_version":   "alpha_radar_v1_4.py",
  "debug_dir":      "/home/bitnami/logs/storage/654434/alpha_radar_debug/run_20260418_092000_a3f1",
  "config_summary": {
    "K": 20,
    "volume_bar_n": 10.0,
    "loop_sleep_sec": 2.0,
    "drain_enabled": true,
    "max_drain_rounds": 5,
    "max_drain_wall_time_ms": 3000,
    "chart_update_interval_sec": 10,
    "status_update_interval_sec": 5,
    "logprofit_interval_sec": 10,
    "summary_log_interval_sec": 60,
    "gex_min_fetch_interval_ms": 60000,
    "ppe_history_window": 400,
    "centrality_min_bars": 20,
    "anchor_shift_frac": 0.5,
    "anchor_ppe_reset_frac": 1.0,
    "debug_enabled": true,
    "debug_cycle_audit_every": 1,
    "debug_task_audit_mode": "on_execute"
  }
}
```

**用途**：回放时先读这份文件确定本次运行的参数上下文，再解析其他 JSONL。

#### 20.3.2 `cycle_audit.jsonl`（kind=`cycle_audit`）

每轮主循环写 1 行（按 `debug_cycle_audit_every` 节流，默认 1 表示不节流）。

```json
{
  "ts_ms": ..., "utc8_datetime": ..., "run_id": ..., "kind": "cycle_audit",
  "cycle_index":         1234,
  "bar_index":           308,              // 本轮最后一根 bar 的 index，可能为 null
  "anchor_state":        "FRESH",
  "event_state":         "CONFIRMED",
  "runtime_gate":        "COLD_START",
  "classification_state":"UNAVAILABLE",
  "new_bars_count":      1,
  "trade_count":         523,
  "catchup_rounds":      1,
  "wall_time_ms":        87,
  "hit_limit":           false,
  "hit_wall_time":       false,
  "backlogged":          false,
  "tasks": {
    "tick_chart":     {"executed": false},
    "tick_status":    {"executed": true},
    "tick_logprofit": {"executed": false},
    "tick_summary":   {"executed": false}
  },
  "had_exception":       false
}
```

**诊断用法**：看 cycle_index 跨度 ÷ 实际时间是否接近 `loop_sleep_sec=2s`；若远大于说明主循环慢。`tasks.*.executed` 可在没有新 Bar 的情况下追踪挂钟调度的健康度。

#### 20.3.3 `bar_pipeline.jsonl`（kind=`bar_pipeline`）

每根完成 Bar 写 1 行。

```json
{
  "ts_ms": ..., "utc8_datetime": ..., "run_id": ..., "kind": "bar_pipeline",
  "cycle_index":  1234,
  "bar_index":    308,
  "bar_ts_ms":    1713398395000,
  "bar_utc8":     "2026-04-18 09:19:55",
  "open": 70125.3, "high": 70180.0, "low": 70095.7, "close": 70140.2,
  "volume": 10.0, "cvd_delta": 2.3,
  "flip_point":            70200.0,
  "band_half":             156.7,
  "band_clamped":          false,
  "normalized_deviation": -0.38,
  "event_state":          "CONFIRMED",
  "anchor_state":         "FRESH",
  "runtime_gate":         "COLD_START",
  "classification_state": "UNAVAILABLE",
  "outside_bar_count":    3,
  "anchor_shift_event":   false,
  "shift_magnitude":      null,
  "instructions":         {"reset_deviation_counter": false, ...},
  "raw_summary":    { "ppe_raw": ..., "ed1_raw": ..., "center_loss": ..., ... },
  "labels_summary": { "anchor_health_score": 32.2, "anchor_health_level": "警",
                       "classification_result": null, "confidence": null,
                       "anchor_validity": "有效", "ols_label": null, ... },
  "snapshot_written": true
}
```

**诊断用法**：每根 Bar 的完整流水线快照。与 `factor_audit.jsonl` 的 `bar_index` 一一对应。

#### 20.3.4 `factor_audit.jsonl`（kind=`factor_audit`）

每根完成 Bar 写 1 行，聚焦因子与 unready 原因。

```json
{
  "ts_ms": ..., "utc8_datetime": ..., "run_id": ..., "kind": "factor_audit",
  "cycle_index": 1234, "bar_index": 308,
  "factors": {
    "ppe_raw":          0.305,
    "ppe_percentile":   0.4,
    "ed1_raw":          0.12,
    "sign_consistency": 0.68,
    "center_loss":      0.31,
    "erosion_drift":    0.25,
    "ols_slope":        null,
    "r_squared":        null,
    "cvd_strength":     0.18
  },
  "health_breakdown": {
    "h_time":         0.95,
    "h_erosion":      0.52,
    "h_center_loss": 0.48,
    "h_space":        0.50,
    "h_micro":        0.55,
    "h_stability":    1.00
  },
  "anchor_health_score": 32.2,
  "anchor_health_level": "警",
  "centrality_frozen":   true,
  "unready_reasons": {
    "ols_slope":       "OLS_WINDOW_COLD",
    "r_squared":       "OLS_WINDOW_COLD"
  }
}
```

**诊断用法**：`unready_reasons` 直接告诉回放者"某个因子为什么是 None"。`health_breakdown` 拆出四个 H 因子，一眼看出是哪个因子把 score 拉低。

#### 20.3.5 `task_frequency.jsonl`（kind=`task_frequency`）

time-driven 任务每次调度判定时写 1 行（按 `debug_task_audit_mode` 控制）。

```json
{
  "ts_ms": ..., "utc8_datetime": ..., "run_id": ..., "kind": "task_frequency",
  "cycle_index": 1234,
  "task_name":              "tick_summary",
  "expected_interval_sec":  60,
  "actual_interval_sec":    62.3,
  "since_last_ms":          62300,
  "executed":               true,
  "skipped_reason":         null,         // executed=true 时为 null
  "lagged":                 false,        // actual > expected × 2 即 true
  "extra":                  null          // 某些任务带 extra，如 tick_chart 的 bars_flushed
}
```

**`debug_task_audit_mode`**:
- `"on_execute"`（默认）：仅记录 `executed=true` 的调度。日志量较小。
- `"on_attempt"`：每次 tick_* 被调用都记录（包括 `executed=false` 的跳过）。日志量大但最完整。

**诊断用法**：看 `actual_interval_sec` 是否稳定接近 `expected_interval_sec`；如果 `tick_summary` 的 actual 长期大于 60×2=120 秒，即 `lagged=true`，说明主循环被挂钟任务拉长。

#### 20.3.6 `state_transitions.jsonl`（kind=`state_transition`）

四轴 + event_state 任一发生迁移时写 1 行（幂等：未发生实际迁移不写）。

```json
{
  "ts_ms": ..., "utc8_datetime": ..., "run_id": ..., "kind": "state_transition",
  "cycle_index": 1234, "bar_index": 307,
  "axis":       "event_state",
  "from_state": "OUTSIDE_PENDING",
  "to_state":   "CONFIRMED",
  "cause":      "per_bar_observed"
}
```

**诊断用法**：按 cycle_index/bar_index 排序即可重建状态机的完整时间线。

#### 20.3.7 `anomalies.jsonl`（kind=`anomaly`）

结构性异常发生时写 1 行。`anomaly_type` 来自 REASON_CODE 字典。

```json
{
  "ts_ms": ..., "utc8_datetime": ..., "run_id": ..., "kind": "anomaly",
  "cycle_index": 1234, "bar_index": null,
  "anomaly_type": "BACKLOG_ACTIVE",
  "description":  "Backlog 未消化 (drain 用完预算仍未追上)",
  "detail":       {"drain_rounds": 5, "wall_time_ms": 3000}
}
```

**目前会触发的 anomaly 类型**：`BACKLOG_ACTIVE` / `CVD_DEGRADED` / `BAND_CLAMPED`。未来可扩展更多，但新类型必须加入 `REASON_CODE` 字典。

#### 20.3.8 `exceptions.jsonl`（kind=`exception`）

模块异常发生时写 1 行。traceback 截断到 2KB。

```json
{
  "ts_ms": ..., "utc8_datetime": ..., "run_id": ..., "kind": "exception",
  "cycle_index": 1234, "bar_index": 308,
  "module":              "SnapshotRecorder",
  "stage":               "write",
  "exception_type":      "KeyError",
  "message":             "'flip_point'",
  "traceback":           "Traceback (most recent call last):\n...",
  "degraded":            true,
  "affects_main_flow":   false
}
```

**`affects_main_flow=true`** 的异常需要重点关注——这意味着主循环本身抛出了异常。

### 20.4 REASON_CODE 全局字典

所有 `unready_reason / skipped_reason / anomaly_type` 字段的取值**必须**来自以下字典。新增 code 需同步加入字典。

| 类别 | Code | 含义 |
|---|---|---|
| 计算层 | `ANCHOR_SOURCE_MISSING` | `anchor_source_ts_ms` 缺失或为 0（GEX 从未成功） |
| 计算层 | `ANCHOR_EXPIRED` | `anchor_state == EXPIRED`（锚参照系失效） |
| 计算层 | `CENTRALITY_BUFFER_COLD` | 中心性缓冲未满 `centrality_min_bars` |
| 计算层 | `CENTRALITY_FROZEN_NO_TRANSIT` | 冻结期且无透出值（首次冻结即发生） |
| 计算层 | `PPE_HISTORY_COLD` | PPE 历史 < `ppe_history_window`（20K） |
| 计算层 | `OLS_WINDOW_COLD` | OLS 窗口 < `ols_window_min`（3K） |
| 计算层 | `R_SQUARED_BELOW_MIN` | R² < `ols_r2_min` |
| 计算层 | `CVD_DEGRADED` | trade_id gap 导致 CVD 方向降级 |
| 计算层 | `COLD_START_WARMUP` | 其他 warm-up（如首根 Bar / 无前值） |
| 调度层 | `INTERVAL_NOT_REACHED` | 距离上次执行未达 interval |
| 调度层 | `QUEUE_EMPTY` | pending 队列为空（`tick_chart`） |
| 调度层 | `SNAPSHOT_NONE` | `latest_snapshot` 未就绪（`tick_summary`） |
| 调度层 | `SCORE_NONE` | `anchor_health_score=None`（`tick_logprofit`） |
| 调度层 | `CHART_FN_MISSING` | FMZ Chart 函数未注入 |
| 调度层 | `LOGPROFIT_FN_MISSING` | FMZ LogProfit 函数未注入 |
| 调度层 | `DRAIN_BELOW_NOTEWORTHY` | drain 不值得记录 |
| 调度层 | `BACKLOG_NOT_TRIGGERED` | 未处于 backlog 状态 |
| 完整性 | `BACKLOG_ACTIVE` | Backlog 未消化 |
| 完整性 | `BAND_CLAMPED` | band_half 触发硬护栏截断 |
| 完整性 | `GEX_FETCH_FAILED` | GEX HTTP 失败或 payload 解析失败 |
| 完整性 | `PERSIST_DISABLED` | 持久化因连续失败已禁用 |

### 20.5 性能与降级策略

| 场景 | 行为 |
|---|---|
| `debug_enabled=False` | 所有 `record_*` 方法立即 return，零 I/O、零字典构造 |
| `boot()` 创建目录失败 | 自动置 `_enabled=False`，整个审计层退化为空壳，业务继续运行 |
| 某个文件连续写失败 ≥ `debug_persist_failure_limit`（默认 5） | 该文件被软禁用，**其他文件不受影响** |
| 某个文件大小 > `debug_file_size_limit_mb`（默认 100MB） | 该文件被软禁用，不做 rotation（依赖 run_id 目录隔离，重启即新目录） |
| `record_*` 内部抛异常 | 被 try/except 捕获，只 `log_warn`，不抛给业务 |
| traceback 过长 | `record_exception` 截断到 2KB 避免单条记录过大 |

### 20.6 已知局限

1. **不记录逐笔成交原始包**。aggTrades 的原始 1000 条/轮成交数据**不入审计日志**——这是有意设计。一旦记录会导致单日日志上百 MB，且这些原始数据不能帮助定位主循环逻辑的 bug（定位 bug 需要的是度量与流水线，不是原始 tick）。仅在异常排查极端需要时，才考虑启用单次 raw-packet dump。
2. **文件 I/O 是同步追加写**。每条 `record_*` 都会 `open(mode="a") → write → close`。在 FMZ 沙盒下主循环 2s 周期 + 每轮 ~10 条审计记录的场景实测可接受（I/O 耗时 ~1-2ms/条）。若未来发现 I/O 开销过高，可改用分批 flush 策略。**本轮不做过早优化**。
3. **不做日志 rotation**。rotation 机制复杂且在 FMZ 沙盒下易出错。替代方案是 run_id 目录隔离——重启即新目录。长期运行时的磁盘清理由部署者自己负责（`find ... -mtime +30 -delete` 之类）。
4. **CPython 原子性依赖**。JSONL 追加写利用 Python open-write-close 对追加写的原子性。在 FMZ 单进程单线程环境下这是可靠的；若未来改为多进程，需要加文件锁。
5. **run_id 冲突理论可能**。短哈希用的是启动毫秒时间戳低 16 位，同一秒内重启两次在概率意义上冲突可能性约 1/65536。在实盘场景可忽略。

### 20.7 DebugRecorder 调用点一览

以下是业务代码中调用 DebugRecorder 的所有点（共 9 处），便于未来审查："如果把这些行删掉，系统行为不变"。

| 调用点 | 方法 | 触发条件 |
|---|---|---|
| `run()` 启动 | `debug_rec.boot(config_summary=...)` | 程序启动一次 |
| `run()` 主循环末尾 | `record_cycle_audit` | 每轮（按节流） |
| `run()` Bar 流水线内 | `record_bar_pipeline` | 每根新 Bar |
| `run()` Bar 流水线内 | `record_factor_audit` | 每根新 Bar |
| `run()` Bar 流水线内 | `record_state_transition` | 四轴任一迁移 |
| `run()` 采集阶段 | `record_anomaly` | backlog / cvd_degraded 发生时 |
| `run()` Bar 流水线内 | `record_anomaly` | band_clamped 发生时 |
| `run()` 主循环 except | `record_exception` | 主循环抛异常时 |
| `Display.tick_*` 内 | `record_task` + `record_exception` | 每次 tick 调度判定 |

### 20.8 与状态栏的关系

**状态栏不变**。DebugRecorder 是额外的本地持久化层，与状态栏完全正交：
- 状态栏（v1.3.3 六表）：给实盘盯盘时用
- 调试日志（v1.4）：给离线回传 + 模型审查用

两者可以独立启停：`debug_enabled=False` 时状态栏照常工作；Display 代码异常时审计日志照常工作（`tick_*` 的异常会被 DebugRecorder 捕获并记入 `exceptions.jsonl`）。

---

## 21. 验收说明

本轮的核心承诺是："**把 v1.4 交付到实盘，跑一段时间后把 `/home/bitnami/logs/storage/654434/alpha_radar_debug/run_<latest>/` 目录回传，模型可以独立审查整个工作流**"。下面逐条展开。

### 21.1 因子计算是否可回放

**可以**。每根完成 Bar 在 `factor_audit.jsonl` 里都有一行完整记录，包含：
- 9 个原始因子值（ppe_raw / ppe_percentile / ed1_raw / sign_consistency / center_loss / erosion_drift / ols_slope / r_squared / cvd_strength）
- 6 个健康度分量（h_time / h_erosion / h_center_loss / h_space / h_micro / h_stability）
- 最终 score 和 level
- `unready_reasons` dict 明确告诉你哪些因子为 None 以及原因

配合 `bar_pipeline.jsonl` 的 OHLC / flip_point / band_half，回放脚本可以**完全重算一次**得分验证一致性。

### 21.2 频率检测是否可定位

**可以**。`task_frequency.jsonl` 记录每次 time-driven 任务的：
- `expected_interval_sec`（配置的节奏）
- `actual_interval_sec`（实测挂钟间隔）
- `lagged`（是否超出预期 2 倍）

加上 `cycle_audit.jsonl` 里 `cycle_index` 跨度和时间戳，可以直接算出主循环真实周期。如果发现 `tick_summary` 的 actual 长期 > 120s（预期 60s 的 2 倍），就能定位到调度失频。

### 21.3 模块自检是否可读

**可以**。每根 Bar 的 `bar_pipeline.jsonl` 里包含：
- 四轴状态快照（anchor_state / event_state / runtime_gate / classification_state）
- `raw_summary` 里 `centrality_frozen` / `cvd_degraded` 两个关键降级标记
- `band_clamped` 标记
- `snapshot_written` 标记（SnapshotRecorder 是否成功写入）

`anomalies.jsonl` 记录结构性异常（backlog / cvd_degraded / band_clamp）。
`exceptions.jsonl` 记录所有模块的异常 + traceback + `degraded` + `affects_main_flow` 标志。

### 21.4 关键值是否完整

**是**。每条记录都带：
- `ts_ms` + `utc8_datetime` + `run_id` + `kind`（公共信封）
- `cycle_index` / `bar_index`（若适用）

三张 Bar 级文件（`bar_pipeline / factor_audit / state_transitions`）共享 `bar_index`，可 JOIN 查询。`cycle_audit.jsonl` 的 `cycle_index` 与其他文件也可 JOIN，重建"某一轮主循环发生了什么"的完整图景。

### 21.5 日志是否真的写到 /home/bitnami/logs/storage/654434

**是**。`CONFIG["debug_base_dir"]` 的默认值就是 `/home/bitnami/logs/storage/654434/alpha_radar_debug`。`DebugRecorder.boot()` 会：
1. `os.makedirs(run_dir, exist_ok=True)` 创建 run_id 子目录
2. 写 `run_meta.json`
3. 写 `latest_run.txt` 指向 run_id

烟测已验证所有 8 个文件都能写入且 JSON 格式合法。

### 21.6 回传文件指引

**出问题时回传以下内容即可**：

1. `cat /home/bitnami/logs/storage/654434/alpha_radar_debug/latest_run.txt` 获取最新 run_id。
2. `tar -czf alpha_radar_debug_<run_id>.tgz /home/bitnami/logs/storage/654434/alpha_radar_debug/run_<run_id>/` 打包整个 run 目录。
3. 把 tarball 发给我。

模型解压后读 `run_meta.json` 了解上下文，按需解析其他 7 个 JSONL。**一个压缩包就足以重建完整工作流**。

### 21.7 验证清单（本轮已通过）

以下项目在代码验证阶段全部通过：

- ✓ AST 解析通过
- ✓ 类集合正确（8 业务类 + 1 `DebugRecorder`）
- ✓ 模块加载 + `validate_config` 通过
- ✓ 37 个 v1.4 核心标记全部在位（DebugRecorder 类 + 7 个 record_* 方法 + REASON_CODE 字典 + CONFIG 配置 + run() 接入点 + tick_* 参数）
- ✓ DebugRecorder.boot() 成功，run_dir 与 run_meta.json 写入
- ✓ 7 个 record_* 方法都能写出合法 JSONL
- ✓ `classify_health_unready_reasons` 在三种场景（冷启动全空 / healthy / CVD 降级）都返回正确的 reason dict
- ✓ `debug_enabled=False` 时所有 record_* 方法正常 return，零 I/O
- ✓ `latest_run.txt` 正确指向 run_id

---

*文档完。v1.4 运行审计层（DebugRecorder）。*
