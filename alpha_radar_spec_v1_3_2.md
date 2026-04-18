# Alpha Radar — 项目设计规范文档

> 版本：v1.3.2（时效性防御 + 展示语义收口 + 健康度观测出口）
> 前版：v1.3.1 → v1.3 → v1.2 → v1.1 → 封版 v1.0
> 性质：研究型观测系统，非交易执行系统

---

## v1.3.2 版本变更摘要

### 本轮主题（一句话）

在不打破"主循环只做调度"纪律的前提下，**收口成交采集的时效性隐患、展示层的缺值语义、以及健康度观测出口**——三件事统一围绕"观察者看到的是实时市场还是滞后的旧市场"这个根本问题。

### 修复与增强清单

| 编号 | 类型 | 模块 | 关键变更 |
|---|---|---|---|
| E-1 | 工程防御 | BarAssembler | 新增 `poll_with_drain()`——受控 catch-up drain（三道护栏） |
| E-2 | 语义收口 | Display | `_build_health_table` 明确三态语义：正常值 / 未就绪 / 缺值 |
| E-3 | 观测出口重定向 | Display | LogProfit 改输出 `anchor_health_score`，score=None 时跳过 |
| E-4 | 快照扩字段 | SnapshotRecorder | `raw_values` 新增 2 字段（最少而够用） |
| E-5 | UI 轻量增强 | Display | 新增《采集时效性》2 行状态栏表 |

### 不改变的范围

- **状态机四轴定义**：`runtime_gate / anchor_state / event_state / classification_state` 不变
- **事件类型枚举**：`departure_confirmed / gap_closure_confirmed / anchor_shift`，v1.3.2 不新增
- **健康度定位**：`anchor_health_score` 仍然是纯观测，不进入任何门控
- **分类矩阵 / 锚判定逻辑 / 中心性因子计算规则**：全部未动
- **CVD 降级机制**：v1.1 已定稿，v1.3.2 仍然是 trade_id gap 的唯一响应路径
- **v1.3.1 遗留的中心性冻结期 age 语义**：延续延后至 v1.4+ 讨论

### 关键架构判断（实盘数据支撑）

在实盘日志中观察到：系统按 `summary_log_interval_sec=60s` 节流的"综述"日志，实际间隔从 78 秒到 380 秒不等，最极端一次超过 6 分钟。这直接证实：**主循环耗时远大于 2 秒的 `loop_sleep_sec`**。在此条件下，传统"每轮一次 `poll()`"的采集模式会让系统处理"越来越旧的成交"——即便 trade_id 还没出现 gap，Volume Bar 代表的也已经是几分钟前的市场结构。

v1.3.2 的所有设计围绕这个实盘证据展开。

---

## 目录

1. [项目背景](#1-项目背景)
2. [第一性目的](#2-第一性目的)
3. [研究边界](#3-研究边界)
4. [核心术语](#4-核心术语)
5. [研究主链](#5-研究主链)
6. [三个判定层](#6-三个判定层)
7. [因子集](#7-因子集)
8. [数据获取方式](#8-数据获取方式)
9. [模块化设计](#9-模块化设计)
10. [封版补丁细节（v1.0）](#10-封版补丁细节v10)
11. [v1.1 修复日志](#11-v11-修复日志)
12. [v1.2 架构迭代日志](#12-v12-架构迭代日志)
13. [v1.3 架构迭代日志](#13-v13-架构迭代日志)
14. [已知局限声明](#14-已知局限声明)
15. [v1.3.1 修补日志](#15-v131-修补日志)
16. [v1.3.2 迭代日志](#16-v132-迭代日志)

---

## 1. 项目背景

[1.1 - 1.3 内容在 v1.0 / v1.1 规范中完整给出，v1.3.2 未变更。]

---

## 2. 第一性目的

[2.1 - 2.3 内容在 v1.0 / v1.1 规范中完整给出，v1.3.2 未变更。]

---

## 3. 研究边界

[3.1 - 3.3 内容在 v1.0 规范中完整给出，v1.3.2 未变更。]

---

## 4. 核心术语

[4.1 - 4.8 核心术语在 v1.0 / v1.1 规范中完整给出，v1.3.2 未变更。]

**v1.3.2 新增术语**：

### 4.9 Backlog（积压）

主循环单轮处理时间 + REST 单次 limit(1000) 的组合不足以追上真实市场流速时，系统处理的是"几分钟前产生、刚被 REST 返回"的旧成交。这是**时效性问题**：Bar 的 OHLC / Volume 内部仍然正确，但代表的是过去某个时间窗口。Backlog 与 trade_id gap 的区别：backlog 时 trade_id 仍然连续，gap 时才真正丢成交。

### 4.10 Catch-up Drain（追赶排水）

在单轮主循环内，`BarAssembler.poll_with_drain()` 允许连续调用 `poll()` 多次，直到以下任一条件：
- 本次 poll 返回 0 条（市场没进步）
- 本次 poll 返回 < limit（已追上队尾）
- 达到最大 poll 次数上限
- 累计耗时达到挂钟超时

此机制解决 backlog，不能解决 gap。

---

## 5. 研究主链

[内容在 v1.0 / v1.1 规范中完整给出，v1.3.2 未变更。]

---

## 6. 三个判定层

[6.1 - 6.3 内容在 v1.0 - v1.3 规范中完整给出，v1.3.2 未变更。]

---

## 7. 因子集

[7.1 - 7.8 因子定义在 v1.0 - v1.3 规范中完整给出，v1.3.2 未新增观测因子。]

---

## 8. 数据获取方式

### 8.1 - 8.5

[v1.0 - v1.1 规范内容不变。]

### 8.6 v1.3.2 新增：主循环频率与采集时效性（结构性说明）

本节是 v1.3.2 对 REST 模型先天局限的系统化说明，也是 §16.3 Catch-up Drain 设计的问题背景。

#### 8.6.1 三层语义的严格区分

v1.3.2 在框架内部**严格区分三层时效性/完整性问题**，不允许混用：

| 层级 | 语义 | trade_id | Bar 结构 | 本轮响应 |
|---|---|---|---|---|
| Backlog | 系统处理旧成交，但数据连续 | 连续 | 正确（只是延迟） | `poll_with_drain` 消除 |
| trade_id gap | REST 单轮装不下，中间成交丢失 | **不连续** | Volume 记账失真 | `cvd_degraded` 标记（v1.1 机制） |
| Volume Bar 语义退化 | 持续 gap 导致"每柱 N BTC"不再代表真实市场推进 | — | **失真累积** | 明确列入已知局限，不硬修 |

#### 8.6.2 REST 先天上限

```
REST 单次返回上限      = agg_trades_limit = 1000 条
主循环基础节奏         = loop_sleep_sec = 2 秒
理论最大补给速率       = 1000 / loop_sleep_sec = 500 笔/秒
实盘主循环实际耗时     = 70 秒 ~ 6 分钟（见实盘日志证据）
实际有效补给速率       = 1000 / 70 ≈ 14.3 笔/秒（传统单次 poll）
BTCUSDT 正常流速       = 5 ~ 20 笔/秒
BTCUSDT 剧烈行情流速   = 50+ 笔/秒
```

结论：**传统单次 poll 模式在慢主循环 + 正常流速下就可能追不上**。catch-up drain 把理论补给速率从 `1000/loop_sec` 提升到 `1000 × max_drain_rounds / loop_sec`（默认 5 倍），显著覆盖正常流速。

#### 8.6.3 本轮不解决的问题

catch-up drain 无法解决两类问题：

1. **持续高流速 > drain 补给速率**（如 100+ 笔/秒的持续剧烈行情）：drain 护栏 C 会中止循环让其他 Step 执行，backlog 仍然存在。系统以 `backlogged=True` 如实告知观察者。
2. **trade_id gap 的数据回补**：REST 按 `fromId` 拉取，跳过的成交不可能回填。只能靠 CVD 降级保护下游判断。

根治方案是 WebSocket，FMZ 沙盒不支持，延后到支持的环境再切换（见 §14 已知局限）。

---

## 9. 模块化设计

### 9.1 - 9.9

[七模块职责定义在 v1.0 - v1.3 规范中完整给出，v1.3.2 未改变模块划分。]

### 9.10 主循环结构（v1.3.2 修订）

```
每轮主循环 (loop_sleep_sec)：

  Step 1: AnchorContext.check_update()
          如有新 GEX 快照则更新，检测 anchor_shift_event
          (v1.3.2 不变: 60s 节流独立于 drain 循环)

  Step 2: BarAssembler.poll_with_drain()          ← v1.3.2 替换
          受控 catch-up drain，三道护栏:
          (A) drain_enabled=False: 退化为 v1.3.1 单次 poll
          (B) 本轮 poll 返回 < agg_trades_limit: 已追上队尾，退出
          (C) drain_rounds >= max_drain_rounds: 硬上限退出
          (D) 累计耗时 >= max_drain_wall_time_ms: 挂钟超时退出
          另加隐式退出条件: poll 返回 0 条

  Step 2b: 节流日志 (v1.3.2 新增)               ← 采集层事件
          如 is_backlogged() → backlog_warn_interval_sec 节流告警
          如 drain_rounds>1 或 wall_time_ms>500 → drain_log_interval_sec 摘要

  对每根新完成的 Bar：

    Step 3: DeviationTracker.update(bar)
    Step 4: SystemStateManager.update(...)
    Step 5: ClassificationEvidence.compute(...)
    Step 6: LabelGenerator.generate(...)
    Step 7: SnapshotRecorder.write(... collection_metrics=cycle_metrics)
            ↑ v1.3.2: 传入本轮 drain 产出的采集度量
    Step 8: Display.on_bar(...)
            ↑ v1.3.2: _update_logprofit 消费 anchor_health_score
            ↑ v1.3.2: _build_collection_table 消费 bar_asm 度量
```

**重要**：同一轮 drain 产出的多根 Bar **共享同一组 `cycle_metrics`**——它们是"同一次采集周期"的产物，度量语义一致。这不是 bug，是设计选择：采集度量表达的是"采集层在本轮工作了多少"，而不是"每根 Bar 各自被采集的情况"。

---

## 10. 封版补丁细节（v1.0）

[v1.0 封版内容不变。]

---

## 11. v1.1 修复日志

[v1.1 修复内容不变。]

---

## 12. v1.2 架构迭代日志

[v1.2 迭代内容不变。]

---

## 13. v1.3 架构迭代日志

[v1.3 迭代内容不变。]

---

## 14. 已知局限声明

[v1.0 - v1.3.1 的已知局限全部保留。v1.3.2 新增以下两条：]

**Catch-up Drain 不能解决高流速下的完整性问题**（v1.3.2 新增）：`poll_with_drain` 把采集层的"有效补给速率"从 `1000 / loop_sec` 提升到 `1000 × max_drain_rounds / loop_sec`（默认 5 倍）。这对"主循环慢 + 中低流速"的常见场景是有效的 backlog 缓解。但若市场流速持续超过 drain 的补给速率上限（约 2500 笔在 3 秒挂钟预算内），backlog 仍然累积，真实 `trade_id gap` 仍然可能发生。根治方案是 WebSocket（逐笔推送），FMZ 沙盒当前不支持。若运行环境支持 WebSocket，应优先切换回 §8.1 描述的原设计方案。

**Volume Bar 成交量语义在持续 gap 下失真**（v1.3.2 新增，本轮不解决）：Volume Bar 的物理动机是"每根柱代表恰好 volume_bar_n BTC 的真实市场推进"。若持续 trade_id gap（REST 跳过部分成交），被跳过的那段 Volume 不会被回补。结果：一根 Bar 实际对应 "N BTC + Σ(lost_qty)" 的真实推进，但系统记账为 N BTC。这会让所有基于 Volume Bar 计算的量（σ_slow、OLS 斜率、CVD 累积、PPE）在持续 gap 期间失去"每柱一单位 Volume"的可比性。`cvd_degraded` 标记能告知观察者 CVD 方向不可信，但不能修正 Bar 内部的 Volume 记账。v1.3.2 不引入 Volume 补偿逻辑，保持当前设计的透明性——观察者看到 `cvd_degraded=True` 时应理解这一段时间内的所有 Volume-time 量都值得怀疑。

---

## 15. v1.3.1 修补日志

[v1.3.1 修补内容不变（Fix-1 到 Fix-6）。]

---

## 16. v1.3.2 迭代日志

v1.3.2 是一次**主题统一的架构轮**，不是打散的 bug 列表。三件事（时效性防御 + 展示语义 + 观测出口）都指向同一个根本问题：**观察者看到的是实时市场，还是滞后的旧市场？**

### 16.1 本轮主题与不变项

**主题**：在主循环慢 + REST pull 模型的先天约束下，让系统（a）在能追的时候主动追；（b）追不上时如实告知；（c）展示层不再给出模糊的"数据缺失"表达；（d）观测出口对齐到最值得看的时间序列——锚健康度。

**不变项**：模块划分（7+1 模块不变）、状态机四轴、事件类型枚举、健康度定位、分类矩阵、中心性因子计算规则。

### 16.2 主循环频率隐患的本质判断

#### 16.2.1 三层语义的因果链

```
主循环慢 (实盘观察 70s - 380s)
    │
    ▼
 Step-A: Backlog
    - 系统处理旧成交
    - trade_id 仍连续
    - Bar 结构仍然对，只是延迟
    - catch-up drain 可以消除
    │
    │ 市场再快 / loop 再慢 / 持续触发
    ▼
 Step-B: trade_id gap
    - REST 单轮 1000 条装不下中间成交
    - CVD 方向被动降级
    - Volume Bar 内部开始"记错账"
    - 不可回补（REST pull 先天局限）
    │
    │ 持续
    ▼
 Step-C: Volume Bar 语义退化
    - "每柱 N BTC"不再代表真实市场推进
    - 所有 Volume-time 量失去可比性
    - 本轮不硬修，写入已知局限
```

**判断**：实盘日志显示系统当前处在 Step-A 但有滑向 Step-B 的风险。本轮最值得修的是 Step-A，且正好能降低 Step-B 发生概率。Step-C 是 Step-B 持续的后果，不是本轮能解决的。

#### 16.2.2 为什么要把 catch-up drain 住进 BarAssembler 而不是 run()

三个理由：

1. **度量产生在采集层**：drain 是否触顶、耗时多少、是否积压——这些是采集行为的属性，数据产权属于 `BarAssembler`。若 drain 循环住在 `run()`，`run()` 就要维护一堆度量变量，破坏"主循环只做调度"纪律。

2. **度量字段自然封装**：`BarAssembler._last_cycle_*` 系列字段生命周期与 drain 循环对齐，getter 暴露只读视图。Display 层消费这些度量不需要触碰 `run()`。

3. **测试边界清晰**：`poll_with_drain()` 是纯粹的"给定 REST 返回行为，得到 Bar 列表 + 度量"映射，可独立烟测。Mock `_fetch_agg_trades` 即可覆盖全部退出条件（实际代码已通过三场景烟测）。

### 16.3 Catch-up Drain 设计（核心交付）

#### 16.3.1 三道硬护栏

```python
# poll_with_drain 内部循环骨架
while True:
    new_bars = self.poll()
    catchup_rounds += 1
    wall_time_ms   += (now - round_start)

    # 护栏 A: drain 被禁用
    if not drain_enabled:
        break

    # 护栏 B: 市场完全没进步（最弱态，正常退出）
    if _last_poll_returned_count == 0:
        break

    # 护栏 C: 本轮未触顶，已追上队尾（最常见，正常退出）
    if _last_poll_returned_count < agg_trades_limit:
        break

    # 触顶了（记录疑似积压信号）
    _last_cycle_hit_limit = True

    # 护栏 D: poll 次数上限
    if catchup_rounds >= max_drain_rounds:
        break

    # 护栏 E: 挂钟超时
    if now >= deadline_ms:
        _last_cycle_hit_wall_time = True
        break
```

#### 16.3.2 护栏选型依据

| 护栏 | 类型 | 目的 |
|---|---|---|
| A: drain_enabled | 配置开关 | 允许完全退化到 v1.3.1 行为（用于对比、降级） |
| B: 返回 0 条 | 隐式退出 | 市场没进步，没必要继续 |
| C: 返回 < limit | 隐式退出 | **最重要的退出**——意味着本批已包含所有积压 |
| D: max_drain_rounds | 硬上限 | 防止"市场比 REST 快时无限循环" |
| E: max_drain_wall_time_ms | 挂钟上限 | 防止单轮 drain 挤占 AnchorContext/Display 的 CPU 份额 |

默认值：
- `max_drain_rounds = 5`（5000 条覆盖约 5 分钟 ×100 笔/分钟流速）
- `max_drain_wall_time_ms = 3000`（2 秒主循环 + 3 秒 drain = 5 秒，在 60s 级 GEX 节奏下仍有 12× 冗余）

这两个数字都是工程经验值，标注为"待实盘校准"。

#### 16.3.3 `is_backlogged()` 判定

```python
def is_backlogged():
    return _last_cycle_hit_limit AND _last_cycle_hit_wall_time
```

**单独触顶 ≠ backlog**：可能只是一次突发成交批，下一轮就追上。
**单独超时 ≠ backlog**：可能只是 REST 网络慢。
**两者同时**才认为 drain 耗尽预算仍没追上，才是真 backlog。

#### 16.3.4 与"主循环只做调度"纪律的关系

run() 仍然只调用一次 `bar_asm.poll_with_drain()`，不知道内部做了几轮 poll。drain 循环封装在 `BarAssembler` 里，对 run() 不可见。

#### 16.3.5 与 GEX 节奏的独立性

`AnchorContext.check_update()` 内部有 60s 节流，drain 循环不调用 GEX API。drain 最坏占用 3 秒挂钟（护栏 E），对 60s GEX 节奏可忽略。

### 16.4 Display 缺值语义三态收口

明确区分三类展示语义：

| 类别 | 含义 | 展示形式 | 触发场景 |
|---|---|---|---|
| 正常可计算值 | 数据完整、系统可计算 | 直接显示数值（如 "0.42 / 良"） | 稳态运行 |
| 未就绪 | **有业务语义**的不可计算 | 显示 level 文本（如 "未就绪"） | COLD_START / EXPIRED |
| 普通缺值 | **无业务语义**的数据缺失 | 显示 "-" | 冷启动期某些中心性因子、事件暂无等 |

**v1.3.1 Fix-2** 已经把 COLD_START / EXPIRED 的 `anchor_health_score` 改为 None，并在状态栏显示 level 文本而非 "-"。v1.3.2 **只做注释和文档的语义收口**，把这套三态规范明确写入 `_build_health_table` 的 docstring，同时在本文档 §16.4 固化语义。代码行为本身在 v1.3.1 已经正确，本轮是收口"注释/文档/实际行为"三者一致。

### 16.5 LogProfit 职责重定向

#### 16.5.1 输出内容的选择

v1.3.2 前：LogProfit 输出 `normalized_deviation`（归一化偏差 σ）
v1.3.2 后：LogProfit 输出 `anchor_health_score`（锚健康度，0~100）

**选型理由**：
- LogProfit 是 FMZ 的时间序列曲线出口，最适合表达"单一核心观察量随时间演化"。
- 在 Alpha Radar 的观测目标上，**锚健康度的时间序列变化** 比"当前偏差多少 σ"更有研究价值——健康度下降趋势可能预告脱离，单点偏差数值在状态栏看即可。
- `anchor_health_score` 原始值 0~100 直接输出，不归一化——分档阈值 80/60/40/20 是已经融入规范与 UI 的语义，归一化为 [0,1] 反而增加读图心算负担。

#### 16.5.2 score=None 时的行为

**策略**：不调用 LogProfit，跳过本 tick。

**不这样做的选项与否决理由**：
- 伪造 0 → 错误。0 在连续序列里意味着"最濒危"，与"不可计算"语义完全相反。
- 复用上次值 → 错误。时间序列图上会出现"健康度长时间停在某个值"的假象。
- 总是输出 → 错误。违反"score=None 意味着锚参照系不可用"的规范定义。

**正确的视觉语义**：LogProfit 图上出现断点——健康度从 EXPIRED 前的数值"消失"，恢复后从新数值重新开始。观察者看到断点就知道"这段时间系统不可计算"，与"数值为 0"有本质区别。

#### 16.5.3 偏离信息的职责分离

原先"LogProfit 观察偏离"的功能分散到三个出口：

| 出口 | 观察维度 | 变化 |
|---|---|---|
| 状态栏"因子证据"表"归一化偏差"行 | 瞬时数值 | 保留，不变 |
| 主图右轴"偏差(σ)"曲线 | 时间序列 | 保留，不变 |
| summary_log "偏差={nd}σ" 字段 | 60s 周期日志回看 | 保留，不变 |
| **LogProfit** | **时间序列** | **改为 anchor_health_score** |

偏离信息的三个旧出口都还在。用户想看偏离时间序列，主图右轴副轴仍然呈现；想看当前快照，状态栏；想看日志回放，summary。LogProfit 单独解放出来专注做"轴健康度时序观察站"。

#### 16.5.4 更新频率

沿用现有 `logprofit_interval_sec`（默认 10 秒）。健康度每根 Bar 更新，10 秒节流完全够用，不新增独立频率参数（反膨胀）。

### 16.6 CONFIG 新增项（v1.3.2）

| 项 | 默认值 | 说明 |
|---|---|---|
| `drain_enabled` | True | 是否启用 catch-up drain；False 退化为 v1.3.1 单次 poll 行为 |
| `max_drain_rounds` | 5 | 单轮 drain 最多 poll 次数；5 × 1000 = 5000 条覆盖 5 分钟×100笔/分钟 |
| `max_drain_wall_time_ms` | 3000 | 单轮 drain 挂钟上限；loop_sleep_sec=2 下留 1 秒净开销 |
| `drain_log_interval_sec` | 60 | drain 摘要日志最小间隔，防刷屏 |
| `backlog_warn_interval_sec` | 30 | backlog 告警最小间隔，防刷屏 |

新增自由度 = 5 个，全部围绕采集时效性（无重叠语义）。

### 16.7 raw_values 表新增字段（v1.3.2）

| 字段 | 类型 | 含义 |
|---|---|---|
| `poll_wall_time_ms` | int \| None | 本柱被打包时，本轮 drain 累计挂钟耗时 |
| `catchup_rounds_used` | int \| None | 本柱被打包时，本轮 drain 执行的 poll 次数（1=单次） |

**为什么只加 2 个字段**：
- `trade_count` 和 `bar_count` 可从 Bar 序列反推（每快照帧对应一根 Bar）。
- `backlogged` 是标签层衍生量，在 Display 展示即可，不入快照（避免 backlog 信号污染历史）。
- `hit_wall_time` / `hit_limit` 过于底层，回放价值低。
- `last_poll_had_gap` 已由现有 `cvd_degraded` 字段间接表达。

**同一轮 drain 产出的多根 Bar 共享同一组度量**：设计选择，不是 bug。度量表达的是"采集层本轮的状态"。

### 16.8 不新增的字段/模块/状态轴

**labels 表**：无新增字段。
**events 表**：无新增事件类型（backlog 不是"事件"，是"采集状态"）。
**状态轴**：不新增 `collection_state` 等新轴（backlog 不进 FSM，不影响分类可用性）。
**模块**：不新增监控模块或采集管理器。

### 16.9 反过拟合审查

逐条对照：

1. **不做成"监控字段膨胀版"**：raw_values 只新增 2 字段，放弃 6-8 个候选；状态栏只加 1 个轻量 2 行表。
2. **不让 backlog 污染健康度**：`LabelGenerator` 对 backlog 零感知。`H_time × H_space × H_micro × H_stability` 四因子不引入任何采集层信号。
3. **LogProfit 改造单向依赖**：Display 读取 `labels["anchor_health_score"]`，`LabelGenerator` 不感知 LogProfit 存在。
4. **不新增重复语义参数**：drain 的 5 个 CONFIG 项与现有 CONFIG 零重叠；LogProfit 沿用 `logprofit_interval_sec`。
5. **不引入无界 catch-up**：三道硬护栏（`rounds / wall_time / 零增量`）缺一不可；默认 5 轮 + 3000ms 双重上限。
6. **不用"先进术语"掩盖经验值**：`max_drain_rounds=5`、`max_drain_wall_time_ms=3000` 都标注为"工程经验值，待实盘校准"，理由在代码注释和本规范中均明确说明。

### 16.10 实盘校准建议

部署 v1.3.2 后，建议观察以下指标 ≥ 2 周，判断是否需要调整默认值：

- `catchup_rounds_used` 分布：若绝大多数 = 1，说明 drain 未触发，但也意味着实盘主循环够快（好事）；若长期 > 3，说明每轮都在重度追赶（`max_drain_rounds=5` 偏紧）。
- `poll_wall_time_ms` 分布：若 P95 接近 3000ms，护栏 E 经常触发，考虑加大预算或优化上游调用链。
- `is_backlogged()` 频率：若每日 > 10 次告警，说明流速已持续超过 drain 补给能力，应考虑切换 WebSocket 环境。
- `cvd_degraded` 频率：与 backlog 频率对比，判断 gap 发生与 backlog 发生的比例。

---

*文档完。v1.3.2 时效性防御 + 展示语义收口 + 健康度观测出口。*
