# Alpha Radar — 项目设计规范文档

> 版本：v1.3.3（任务调度边界收口 + 数据采集状态栏增强）
> 前版：v1.3.2 → v1.3.1 → v1.3 → v1.2 → v1.1 → 封版 v1.0
> 性质：研究型观测系统，非交易执行系统

---

## v1.3.3 版本变更摘要

### 本轮主题（一句话）

把 v1.3.2 之前**错误挂在 `on_bar()` 下**的四个时间驱动任务（chart / status / logprofit / summary）迁移到 `run()` 级别的挂钟调度器，并把状态栏《采集时效性》重构为更完整的《数据采集》表。

### 审计判断（修复动因）

v1.3.2 之前的代码里，`chart_update_interval_sec` / `logprofit_interval_sec` / `summary_log_interval_sec` 三个节流参数**看起来**像时间驱动，但它们的入口函数都挂在 `Display.on_bar()` 下面。由于 `on_bar()` 只在新 Volume Bar 完成时被调用，这些任务的真实调度是**"新 Bar 到来 AND 过了 interval"的合取**——不是周期性。

实盘观察佐证：在 BTCUSDT 低流速段，`summary_log` 间隔按配置应为 60 秒，实际从 78 秒一路拉到 380 秒。`PPE%` 冷启动期的 `+100.000% / +60.000% / +66.667%` 这种粗糙刻度说明历史 buffer 里只有零星几根 Bar——即系统产 Bar 速率远低于设计预期。在这种低流速下，挂在 `on_bar` 下的"时间任务"实际不跑，状态栏长时间"卡住"。

### 修复与增强清单

| 编号 | 类型 | 模块 | 关键变更 |
|---|---|---|---|
| T-1 | 调度边界收口 | Display + run | 显式划分 time-driven / event-driven；新增四个 `tick_*()` 方法 |
| T-2 | 缓存模型 | Display | `latest_*` 缓存 + `pending_chart_queue` |
| T-3 | Chart 原语拆分 | Display | `_update_chart` → `_init_chart_object` + `_add_one_chart_row` + `_draw_chart_flags_if_any` |
| T-4 | 时间格式 helper | utils | `fmt_datetime_ms_utc8()` 输出完整日期 |
| T-5 | 状态栏扩表 | Display | 《采集时效性》2 行 → 《数据采集》5 行 |
| T-6 | 最小必要计数器 | AnchorContext / BarAssembler / run() | 补齐 getters 与 cycle_index |

### 不改变的范围

- **状态机四轴**：`runtime_gate / anchor_state / event_state / classification_state` 定义不变。
- **分类矩阵、Anchor 判定、健康度观测语义**：完全不动。
- **poll_with_drain 核心机制**：三道护栏保持 v1.3.2 设计。
- **Chart Flag 事件驱动语义**：三类离散事件的 Flag 打标逻辑不变，仍然按真实 `bar.ts_ms` 对齐。
- **v1.3.2 的已知局限**：backlog / trade_id gap / Volume Bar 语义退化三层语义区分保持。

---

## 目录

1. 项目背景 / 2. 第一性目的 / 3. 研究边界 / 4. 核心术语 / 5. 研究主链
6. 三个判定层 / 7. 因子集 / 8. 数据获取方式 / 9. 模块化设计
10. 封版补丁 v1.0 / 11. v1.1 修复日志 / 12. v1.2 架构迭代 / 13. v1.3 架构迭代
14. 已知局限声明 / 15. v1.3.1 修补日志 / 16. v1.3.2 迭代日志
**17. v1.3.3 迭代日志（本版）**

---

## 1-13 节

章节内容在 v1.0 至 v1.3.2 规范中完整给出，v1.3.3 未变更。仅 §9.10 主循环结构在 §17.3 中有修订版本。

---

## 14. 已知局限声明

[v1.0 - v1.3.2 的已知局限全部保留。]

**v1.3.3 未新增已知局限**——本轮所有改动都是"修正既有实现与既有意图的偏离"，不触碰根本约束。

---

## 15. v1.3.1 修补日志

[v1.3.1 内容不变（Fix-1 到 Fix-6）。]

---

## 16. v1.3.2 迭代日志

[v1.3.2 内容不变（E-1 到 E-5）。]

---

## 17. v1.3.3 迭代日志

### 17.1 审计结论

v1.3.2 及之前版本，`Display.on_bar()` 承担了本不该承担的任务调度职责。具体问题：

```
v1.3.2 的 on_bar() 调用链:
  on_bar(snapshot, ...)
    ├─ _update_chart(...)        ← 内含 chart_update_interval_sec 节流
    │   └─ (还在这里打 Flag, 事件驱动逻辑也藏在这里)
    ├─ _update_logprofit(...)    ← 内含 logprofit_interval_sec 节流
    ├─ _update_status(...)        ← 每次新 Bar 无条件刷新状态栏
    ├─ _maybe_emit_state_log(...) ← 真·事件驱动 (状态变化去重)
    └─ _maybe_emit_summary(...)  ← 内含 summary_log_interval_sec 节流
```

问题矩阵：

| 任务 | 本该是什么 | 实际是什么 | 后果 |
|---|---|---|---|
| chart 线条 | time-driven (每 10s) | event-driven (每新 Bar 时 + interval 判断) | 低流速下长时间不更新 |
| status 刷新 | time-driven (wall-clock 最新状态) | event-driven (每新 Bar) | 低流速下看起来"卡死" |
| logprofit | time-driven (均匀时间序列采样) | event-driven + interval | 采样点疏密随 bar 节奏 |
| summary | time-driven (每 60s) | event-driven + interval | 间隔从 60s 拉到 380s |
| chart flags | event-driven | event-driven | **正确**，保持 |
| state_log | event-driven (去重) | event-driven (去重) | **正确**，保持 |

**结论**：前四项必须迁出 `on_bar()`，交给 `run()` 级别的挂钟调度。后两项保留在 `on_bar()` 内。

### 17.2 任务-频率-入口 清单（v1.3.3 定稿）

| 任务 | 调度类型 | 触发条件 | 入口方法 | 频率参数 |
|---|---|---|---|---|
| **Chart 线条刷新** | time-driven | 每 `chart_update_interval_sec` 秒 | `Display.tick_chart()` | 10s |
| **状态栏刷新** | time-driven | 每 `status_update_interval_sec` 秒 | `Display.tick_status()` | 5s（v1.3.3 新增） |
| **LogProfit 输出** | time-driven | 每 `logprofit_interval_sec` 秒 | `Display.tick_logprofit()` | 10s |
| **综述日志** | time-driven | 每 `summary_log_interval_sec` 秒 | `Display.tick_summary()` | 60s |
| **Backlog 告警** | time-driven (节流) | 每 `backlog_warn_interval_sec` 秒且 backlogged=True | `run()` inline | 30s |
| **Drain 摘要日志** | time-driven (节流) | 每 `drain_log_interval_sec` 秒且 drain 值得记录 | `run()` inline | 60s |
| GEX 轮询 | time-driven (节流) | 每 `gex_min_fetch_interval_ms` | `AnchorContext.check_update()` | 60s |
| aggTrades 轮询 | time-driven + drain | 每 `loop_sleep_sec` + drain 多轮 | `BarAssembler.poll_with_drain()` | 2s × drain |
| Bar 完成 | 物理触发 | 累积 `volume_bar_n` BTC 成交 | `BarAssembler._ingest_trade` 内部 | volume-driven |
| **FSM / evidence / labels 流水线** | event-driven | 每根新 Bar | `run()` 内 for bar in new_bars | per-bar |
| **快照写入** | event-driven | 每根新 Bar | `SnapshotRecorder.write()` | per-bar |
| **Chart Flags** | event-driven | 三类离散事件发生的那根 Bar | `Display._draw_chart_flags_if_any()` | per-event |
| **事件日志** | event-driven | 脱离确认 / 缺口回补等 | `run()` inline | per-event |
| **状态变化去重日志** | event-driven | 四元组状态 key 变化 | `Display._maybe_emit_state_log()` | per-state-change |

### 17.3 主循环结构（v1.3.3 修订）

```
每轮主循环 (loop_sleep_sec):

┌─ 采集阶段 ─────────────────────────────────────┐
│ 1. cycle_index += 1                            │
│ 2. anchor_ctx.clear_cycle_flag()               │
│ 3. anchor_ctx.check_update()       [60s 节流]  │
│ 4. bar_asm.poll_with_drain()       [drain 循环]│
└────────────────────────────────────────────────┘
         │
         ▼
┌─ 采集层节流日志 (time-driven) ─────────────────┐
│ 5. backlog 告警   [30s 节流]                   │
│ 6. drain 摘要     [60s 节流]                   │
└────────────────────────────────────────────────┘
         │
         ▼
┌─ Event-driven 流水线 (仅对新 Bar 执行) ────────┐
│ for bar in new_bars:                           │
│   A. DeviationTracker.update                   │
│   B. SystemStateManager.update                 │
│   C. ClassificationEvidence.compute            │
│   D. LabelGenerator.generate                   │
│   E. SnapshotRecorder.write                    │
│   F. 事件日志 (gap_closure/departure)          │
│   G. display.on_bar():                         │
│        • 更新 latest_* 缓存                     │
│        • 入队 pending_chart_queue               │
│        • 打 Chart Flags (event)                │
│        • 状态变化去重日志 (event)               │
└────────────────────────────────────────────────┘
         │
         ▼
┌─ Time-driven 调度 (不依赖是否有新 Bar) ────────┐
│ 7. display.tick_chart()       [10s 节流]       │
│ 8. display.tick_status()      [5s 节流]        │
│ 9. display.tick_logprofit()   [10s 节流]       │
│ 10. display.tick_summary()    [60s 节流]       │
└────────────────────────────────────────────────┘
         │
         ▼
 time.sleep(loop_sleep_sec)
```

**关键保证**：

- 第 7~10 步**无论本轮是否有新 Bar 都会被调用**，节流由 `tick_*()` 内部自行判断。
- `tick_chart()` 的线条输出只消费 `pending_chart_queue` 中的真实 Bar 点——**队列空则不 add**，**不伪造墙钟点**。
- `tick_logprofit()` 在 `anchor_health_score=None` 时跳过——**不伪造 0**，**不复用旧值**。
- `tick_summary()` 在 `latest_snapshot=None` 时跳过——**不输出空壳综述**。
- `tick_status()` 总是执行——但状态栏的所有显示值都从缓存或实时 getter 取，**缺值时显示 "-" 或 "未就绪"**。

### 17.4 Chart 原语拆分（T-3）

v1.3.2 的 `_update_chart` 是一个 80 行的"肥方法"，同时承担了：
- chart 对象初始化
- 线条节流
- 逐点 add
- Flag 打标

v1.3.3 拆成三个职责单一的方法：

```
_init_chart_object(chart_fn)           ← 一次性初始化, 被 tick_chart 和
                                         _draw_chart_flags_if_any 共享
_add_one_chart_row(raw_row)            ← 把单根 Bar 点 add 到各 series
                                         (仅 tick_chart 的 flush 循环调用)
_draw_chart_flags_if_any(raw_row, ...) ← Flag 判定与 add (仅 on_bar 调用)
```

**关于 lazy init**：`_draw_chart_flags_if_any` 在 chart 尚未初始化时会做一次 lazy init。这**不违反 time-driven 纪律**——flags 本就是 event-driven，事件发生时就应打标。若要求等 `tick_chart` 首次触发才初始化 chart，会丢失冷启动首次 Flag。lazy init 是 event-driven 任务的合理副作用。

### 17.5 Display 缓存模型（T-2）

```python
class Display:
    def __init__(self):
        # v1.3.3: on_bar 刷新的缓存, tick_* 消费
        self._latest_snapshot     = None
        self._latest_dev_state    = None
        self._latest_labels       = None
        self._latest_system_state = None

        # v1.3.3: 按 ts_ms 顺序排队, tick_chart 一次性 flush
        self._pending_chart_queue = []
```

**设计要点**：
- `latest_*` 字段初始化为 `None`，所有 `tick_*()` 在 `None` 状态下优雅跳过。
- `pending_chart_queue` 只入队真实 Bar 的 `raw_row`（含 `ts_ms` 和 `price`）。
- `tick_chart()` 一次性 flush 全部队列后清空——**不重复 add 同一点**。
- 若 `chart_fn` 缺失（非 FMZ 环境），队列被丢弃防止无限堆积。

### 17.6 《数据采集》表五行规范（T-5）

| 行 | 当前值 | 说明 | 数据源 |
|---|---|---|---|
| 成交量柱构造 | `X.XXX / 10.00 BTC (XX.X%)` | `已完成柱=N \| 本轮新柱=M` | `bar_asm.get_current_bar_build_progress()` + `bar_count()` + `get_last_cycle_metrics()` |
| aggTrades 采集 | `每 2.0s \| 本轮×N` | `上次成功 YYYY-MM-DD HH:MM:SS \| 累计调用 M` | `CONFIG.loop_sleep_sec` + `bar_asm.get_poll_call_count_total()` + `get_last_success_fetch_ms()` |
| GEX 采集 | `最小 60s \| 本轮×{0/1}` | `上次成功 ... \| 累计 OK/总 \| 新鲜度 FRESH/STALE/EXPIRED` | `anchor_ctx.was_fetch_attempted_this_cycle()` + 新增四个 getters |
| Backlog / 完整性 | `正常 / 积压 / CVD 降级 / CVD 降级 + 积压` | `语义分离: backlog / trade_id gap / drain 超时` | `bar_asm.is_backlogged()` + `is_cvd_degraded()` + `get_last_cycle_metrics()` |
| 运行轮次 | `#cycle_index` | `总柱=N \| 当前 bar_index=M` | `run()` 的 cycle_index 计数器 + `bar_asm.bar_count()` + `raw_safe.bar_index` |

**保持 v1.3.2 语义纪律**：backlog ≠ trade_id gap ≠ drain 超时。这三者**各自独立显示**，不混为一谈。例如同时发生 backlog 和 CVD 降级时，表头显示 "CVD 降级 + 积压"，说明栏列出每一项具体表现。

### 17.7 v1.3.3 新增计数器与 getter（T-6）

**`AnchorContext`**（5 个新增）：
```
_last_fetch_success_ms             上次成功解析到 GEX 快照的时刻
_fetch_attempt_count_total         累计 HTTP 调用次数
_fetch_success_count_total         累计成功解析次数
_fetch_attempted_this_cycle        本轮主循环是否触发了一次 fetch 尝试

getters:
  get_last_fetch_attempt_ms() / get_last_fetch_success_ms()
  get_fetch_attempt_count_total() / get_fetch_success_count_total()
  was_fetch_attempted_this_cycle()

新方法:
  clear_cycle_flag()                 由 run() 在每轮开始时调用
```

**`BarAssembler`**（2 个新增字段 + 3 个 getters）：
```
_poll_call_count_total             累计 poll 次数 (含 drain 内多轮)
_last_success_fetch_ms             上次 REST 成功拿到 list 响应的挂钟时刻

getters:
  get_current_bar_build_progress()   返回 {current_volume, target_volume, ratio}
  get_last_success_fetch_ms()
  get_poll_call_count_total()
```

**`run()`**：
```
cycle_index                         主循环轮次计数器, 每轮 +1
```

**`utils`**：
```
fmt_datetime_ms_utc8(milliseconds)  → YYYY-MM-DD HH:MM:SS
  (与 fmt_timestamp_ms 并存, 后者仍用于事件流等紧凑场景)
```

### 17.8 CONFIG 新增项

| 项 | 默认值 | 说明 |
|---|---|---|
| `status_update_interval_sec` | 5 | 状态栏 time-driven 刷新周期 |

其余 `chart_update_interval_sec / logprofit_interval_sec / summary_log_interval_sec` 参数**语义没变**——都是"time-driven 调度参数"——但实际调度入口由 `on_bar()` 迁至 `tick_*()` 方法。

### 17.9 不伪造数据的具体兑现

| 场景 | 兑现方式 |
|---|---|
| `pending_chart_queue` 为空 | `tick_chart()` 推进计时器但不 add 任何点 |
| `anchor_health_score=None` | `tick_logprofit()` 推进计时器但不写 LogProfit |
| `latest_snapshot=None` | `tick_summary()` 推进计时器但不输出综述 |
| GEX 还未成功 fetch | 状态栏 "GEX 采集" 行显示 `上次成功 -` |
| BarAssembler 还未成功 poll | 状态栏 "aggTrades 采集" 行显示 `上次成功 -` |
| `catchup_rounds_used` 缺值 | 状态栏 "数据采集" 表相关单元格显示 `-` |
| EXPIRED 锚状态 | `_build_health_table` 显示 `未就绪` 而非伪造分数 |

**纪律总结**：`tick_*` 方法推进计时器**是为了防止每轮主循环都重入判断**（避免瞬时高频重入），但**推进计时器 ≠ 输出数据**。所有 tick 在缺值时跳过输出但继续推进计时器。

### 17.10 反过拟合审查

| 风险 | 规避方式 |
|---|---|
| 新增重型调度框架 | 否。只用 `_last_*_sec` 计时器 + 四个 `tick_*` 方法 |
| 新增 Scheduler 类 / 多层任务系统 | 否 |
| 新增状态轴 | 否。backlog / 采集度量全部在 Display 层消费，不进 FSM |
| 新增快照字段 | 否。v1.3.2 的 2 字段（`poll_wall_time_ms / catchup_rounds_used`）已经够用 |
| 新增事件类型 | 否 |
| 膨胀状态栏 | 仅从 2 行扩到 5 行，每行语义都有明确问题对应 |
| LogProfit 继续污染偏离语义 | 否。v1.3.2 的 "LogProfit 专司锚健康度" 语义延续 |

---

## 18. 验收标准对照

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | 状态栏表标题从《采集时效性》改为《数据采集》 | ✓ | `_build_data_collection_table` 返回 `"title": "数据采集"` |
| 2 | 表中可看到五项 | ✓ | 五行对应五项: 成交量柱构造 / aggTrades 采集 / GEX 采集 / Backlog / 运行轮次 |
| 2a | 成交量柱构造情况 | ✓ | 行 1，格式 `X / Y BTC (Z%)` + 累计柱数 |
| 2b | 数据获取频率 | ✓ | 行 2/3，分别展示 `每 2.0s` / `最小 60s` |
| 2c | API 调用次数 | ✓ | 行 2 显示 `本轮×N \| 累计调用 M`，行 3 显示 `累计 OK/总` |
| 2d | 上次数据获取时间（UTC+8 完整日期型） | ✓ | 行 2/3 使用 `fmt_datetime_ms_utc8` 输出 `YYYY-MM-DD HH:MM:SS` |
| 2e | 运行轮次 | ✓ | 行 5 显示 `#cycle_index`，由 run() 的 cycle_index 计数器提供 |
| 3 | summary_log 不再通过 on_bar() 间接调度，而是 time-driven | ✓ | `on_bar` 中无 `_maybe_emit_summary` 调用；`run()` 末尾调用 `display.tick_summary()`；老定义已删除 |
| 4 | chart 线条刷新不再通过 on_bar() 间接调度，而是 time-driven | ✓ | `on_bar` 中无 `_update_chart` 调用；改由 `on_bar` 入队 + `tick_chart` flush 队列 |
| 5 | chart flags 仍然保持事件驱动，不因本轮重构而错位或重复 | ✓ | `_draw_chart_flags_if_any` 仅在 `on_bar` 调用；`_last_flagged_bar_index` 去重机制保留 |
| 6 | 不修改四轴状态机 / 分类逻辑 / 健康度语义 | ✓ | `SystemStateManager / LabelGenerator / ClassificationEvidence` 未改动 |
| 7 | 单文件仍然干净，没有无必要新实体 | ✓ | 零新类。仅在现有类中增加字段与方法，run() 增加 cycle_index 局部变量 |
| 8 | 给出"任务-频率-入口"清单，明确每个任务的调度方式 | ✓ | §17.2 表格 |

**全部 8 条验收标准通过。**

---

*文档完。v1.3.3 任务调度边界收口 + 数据采集状态栏增强。*
