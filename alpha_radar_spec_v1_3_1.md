# Alpha Radar — 项目设计规范文档

> 版本：v1.3.1（审查修补轮）
> 前版：v1.3 → v1.2 → v1.1 → 封版 v1.0
> 性质：研究型观测系统，非交易执行系统

---

## v1.3.1 版本变更摘要

v1.3.1 是一次**外科式修补轮**，不新增模块、不新增状态轴、不新增事件类型、不改变 v1.3 健康度"纯观测不进入状态机门控"的定位。本轮的唯一目的是关闭审查中确定的 5 个 bug 与 1 项架构未拆开的分层响应，其余开放性讨论项（如中心性冻结期 age 语义）明确延后到 v1.4+。

**六项修补概览**：

| 编号 | 类型 | 模块 | 关键变更 |
|---|---|---|---|
| Fix-1 | Bug | Display | `_maybe_add_event_flag` 补齐 `anchor_shift` 分支 |
| Fix-2 | Bug | LabelGenerator | `anchor_state == EXPIRED` 时 `anchor_health_score = None` / `level = "未就绪"` |
| Fix-3 | Bug | ClassificationEvidence | `ppe_percentile` 改为先算后 append，消除自包含 |
| Fix-4 | 清理 | Display | `_build_health_table` 清除"展示不重算"陈旧注释 |
| Fix-5 | Bug | LabelGenerator | `_make_cvd_label` 在 `normalized_deviation` 极小时返回 `neutral` |
| Fix-6 | 架构补全 | CONFIG + SSM | 拆分 `anchor_shift_frac` 与 `anchor_ppe_reset_frac` 两档阈值 |

**不影响的范围**：
- 状态机四轴定义、事件类型枚举、分类矩阵
- 快照表结构（raw_values / labels / events）
- 健康度观测定位（仍然不门控任何状态转换）
- 中心性因子的计算公式与冻结规则

**详细内容见 §15**。

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

---

## 1. 项目背景

### 1.1 问题起点

加密货币市场，尤其是 BTC/USDT 现货市场，在衍生品规模持续扩张后，价格运动越来越频繁地受到期权做市商（LP）对冲行为的结构性约束。这种约束不均匀分布在价格轴上，而是高度集中在特定的 Gamma 结构节点附近。

传统的技术分析框架将价格视为均质的随机漫步，没有内生的参照坐标系。这导致两个实践问题：

- 带内的小幅震荡被频繁误读为趋势信号，产生大量无效交易噪声。
- 真正的结构性脱离发生时，缺乏基于物理约束的分类依据，无法区分"暂时性缺口"和"定价中心迁移"。

Alpha Radar 是为解决上述第二个问题而设计的观测框架。

### 1.2 设计起点

本框架的认识论起点是：**市场永远是对的，不做预设，用客观可观测的市场行为反向验证锚的有效性。**

GEX 数据（期权 Gamma 敞口分布）给出做市商对冲义务的理论分布，这是锚位置的初始参照。但锚是否真的在约束价格，必须由市场实际成交行为来反向验证，而不是由 GEX 纸面数据直接断言。

### 1.3 与 Gamma Spatial Observer 的关系

Alpha Radar 与此前开发的 Gamma Spatial Observer（v6）在概念层有部分继承关系，主要体现在：

- 吸收带的 band_half 计算公式继承自 Gamma Observer 的 fluid absorption band 设计。
- 带外确认逻辑（2-bar 确认机制）与 Gamma Observer 的 2-tick breakout confirmation 同构。

但 Alpha Radar 的研究目标更宽：Gamma Observer 关注的是四状态 FSM 下的高价值事件捕捉，Alpha Radar 关注的是脱离过程本身的结构分类，不预设哪类事件是"高价值"的。

---

## 2. 第一性目的

### 2.1 工作定义（精确版，内部锚点）

> Alpha Radar 以 GEX 结构识别当前定价锚及其效用强度，以标准化偏离度量价格脱离程度，以 Volume Time 下的价格/CVD 结构判断脱离的微观承载，输出当前脱离状态的结构分类及对应置信度。分类结果为三类：均衡内扰动、暂时性缺口、定价中心迁移。系统不预设锚必然有效，而是将锚效用强度作为第一层输出，并以此加权后续分类的置信度。

### 2.2 系统观测的两类核心事件

**事件一：价格停留在吸收带内的数据支持**

回答的问题：当前带内状态是否有足够支撑，还是正在酝酿脱离。

需要同时回答两个子问题：
- 价格现在是否在带内（状态读取）。
- 当前带内状态的吸收强度如何，以及是否在趋势性变化（支撑质量判断）。

**事件二：价格脱轴后的方向选择**

回答的问题：脱离后更倾向于回归锚，还是持续偏离建立新中心。

需要同时提供三个维度的证据：
- 时间维度：偏离是否在持续扩展（OLS 斜率）。
- 成交维度：脱离是否有真实成交支撑（CVD）。
- 空间维度：脱离过程中是否存在有效阻力（PPE）。

### 2.3 系统输出的本质

系统输出的是**对当前脱离状态的结构分类**，不是看涨/看跌判断，不是交易信号，不是对未来价格的预测。

输出形式是三个问题的顺序回答：

1. 当前是否仍处于锚主导下的局部平衡？
2. 当前是否发生了可观测的脱离？
3. 当前脱离更像暂时性缺口，还是更像定价中心迁移？

---

## 3. 研究边界

### 3.1 本框架不讨论

- 资产长期真实价值。
- 宏观基本面定价。
- 任意市场环境中的统一价格预测。
- 自动交易执行。
- 仅凭单一指标对市场状态作绝对判断。

### 3.2 本框架不假设

- 当前锚一定正确或持续有效。
- 所有脱离都可以被清晰分类。
- 微观成交本身足以独立定义再定价。
- 价格必然回归，或价格必然趋势延续。

### 3.3 非目标声明

- 不解释所有行情。
- 不要求任何时刻都给出分类结论。
- 不把微观成交单独当作结论来源。
- 不把一次偏离自动解释为再定价。
- 不将框架预设为均值回归系统或趋势跟随系统。

---

## 4. 核心术语

### 4.1 当前有效定价锚

在当前时段内，由 GEX 结构约束所刻画、对短周期价格具有参考意义的局部价格中心。"有效"不代表它是真实价值，只代表它在当前观察窗口内具有解释价格分布与偏离的参考意义。

### 4.2 吸收带

围绕当前有效定价锚的价格容忍区间。在该区间内，价格波动优先被解释为均衡内扰动，而非有效脱离。宽度由 band_half 定义，非对称情形暂不引入，使用对称带宽。

### 4.3 脱离

价格相对于当前有效定价锚，出现了超出常规均衡波动范围的偏移。脱离是一个过程概念，至少包含三个层面：空间上离开吸收带、结构上具有持续性、观察上值得进入分类框架。

### 4.4 暂时性缺口 / 可回补缺口

脱离发生后，未能形成新的稳定定价中心，旧锚的解释力重新占优，偏离被吸收回带内。

### 4.5 定价中心迁移

脱离发生后，原有锚对价格的解释力下降，价格围绕新区域形成更稳定的分布，而非重新被旧锚组织。强调的是结构变化，不是单纯位移。

### 4.6 Volume Time（等币量时间）

不使用墙上时钟作为主时间轴，而以固定成交量完成一根 Bar 的方式度量市场推进。目的是消除 REST 轮询抖动、时钟不均匀和低流速阶段对速度、斜率、波动率度量的扭曲。

### 4.7 锚效用强度

一个 [0,1] 的连续量，描述当前 GEX 定义的锚在市场行为层面实际发挥约束作用的强度。由 PPE 百分位（瞬时维度）和带内吸收趋势标签（趋势维度）共同描述。锚效用强度是第一层输出，用于加权后续分类结论的置信度。

### 4.8 缺口回补确认（v1.1 新增）

脱离确认后，价格重新回到带内，需要连续 ≥ 2 根带内 Volume Bar 确认回补有效，防止单根 Bar 触碰带内边界的假闭合。与脱离确认（2 根带外 Bar）构成对称机制。确认期间系统保留分类输出和冻结状态，直至闭合完成。

---

## 5. 研究主链

```
锚建立
  ↓
锚附近波动（均衡内扰动）
  ↓
价格脱离（标准化偏离突破吸收带）
  ↓
脱离扩展  /  脱离衰减
  ↓                ↓
定价中心迁移    回带测试（v1.1: 需 2 bar 确认）
                   ↓
                回补确认  /  回带失败（恢复脱离）
```

这条链不预设方向，不预设结果，只描述结构演化过程。系统的全部因子对应链上的某个节点，没有游离在链外的因子。

---

## 6. 三个判定层

### 6.1 锚判定层

**核心问题**：当前是否存在一个有效的定价锚，以及这个锚的效用强度是多少。

**判定逻辑**：锚的位置由 GEX 结构直接给出，锚的效用强度由市场行为反向验证，两者独立计算，在输出层结合。

**输出**：锚位置 + 锚效用强度描述 + 锚状态标记（正常 / 承压 / 过期 / 重置中）

**空间约束优先原则**：锚判定层的输出是所有后续层的坐标系。如果 anchor_freshness 失效，后续全部分类结论置信度强制降级，不是静默失败。

### 6.2 脱离判定层

**核心问题**：价格是否已经从均衡波动进入值得分类的脱离状态。

**判定逻辑**：脱离是过程状态，不是瞬时阈值。需要同时满足空间条件（偏离幅度超过吸收带）和时间条件（在带外维持 ≥ 2 根 Volume Bar）。

**三阶段设计**（v1.1 修订：从两阶段扩展为三阶段）：
- 候选脱离：标准化偏离 ≥ 1，但带外持续 Bar 计数 < 2。
- 确认脱离：带外持续 Bar 计数 ≥ 2，进入分类判定层。
- 回补待确认：价格从确认脱离状态回到带内，等待连续 ≥ 2 根带内 Bar 确认闭合。期间若价格再次出带，直接恢复确认脱离状态（无需重新走候选阶段）。

**输出**：脱离状态（带内 / 候选 / 确认 / 回补待确认）+ 当前标准化偏离值 + 带外持续 Bar 计数 + 带内持续 Bar 计数

### 6.3 分类判定层

**核心问题**：已发生的脱离，更接近于可回补缺口，还是定价中心迁移。

**判定逻辑**：三个维度独立提供证据，在输出层结合。

**证据优先级**：
- 空间证据有否决权：价格重新回到吸收带内，分类优先倾向均衡内扰动方向。
- 时间维度定方向：OLS 斜率正值倾向迁移，负值倾向回补，R² 不足时时间维度证据无效。
- 成交维度定置信度：CVD 方向与偏离方向一致时，分类置信度提升；反向或中性时，置信度下调。

**分类矩阵**：

| OLS 斜率 | CVD 标签 | 分类结论 | 置信度 |
|---|---|---|---|
| 扩展 | 同向 | 迁移性脱离 | 高 |
| 扩展 | 反向 | 迁移性脱离候选 | 低，标注分歧 |
| 收缩 | 同向 | 暂时性缺口 | 中 |
| 收缩 | 反向 | 可回补缺口 | 高 |
| 震荡 | 同向 | 状态不明确 | 低 |
| 震荡 | 反向 | 倾向回补 | 低 |
| 无效（R²不足） | 任意 | 仅输出 CVD 和 PPE 状态 | 部分 |

**输出**：分类结论 + 置信度等级（HIGH / MEDIUM / LOW / INVALID）

---

## 7. 因子集

> 本节因子定义在 v1.3.1 未发生变更。详见 v1.3 规范文档。关键摘录如下。

### 概览

| 因子 | 所属层 | 角色定位 | 数据来源 |
|---|---|---|---|
| flip_point | 锚判定层 | 锚位置定义 | GexMonitor REST API |
| band_half | 锚判定层 | 吸收带边界 | GexMonitor REST API + Volume Bar σ_slow |
| PPE 百分位 | 锚判定层 | 当前吸收强度（瞬时） | Binance aggTrades REST |
| 带内吸收趋势标签 | 锚判定层 | 吸收强度趋势（历史） | Binance aggTrades REST |
| 标准化偏离 | 脱离判定层 | 带内外状态 + 偏离幅度 | 实时价格 + 锚层 |
| 带外持续 Bar 计数 | 脱离判定层 | 真实脱离确认 | Volume Bar 序列 |
| OLS 斜率（R² 强绑定） | 分类判定层 | 偏离扩展速率（时间维度） | 带外 Bar 序列 |
| CVD 方向（强度门） | 分类判定层 | 成交方向性压力（成交维度） | Binance aggTrades REST（含降级机制） |
| PPE 原始值 | 分类判定层 | 路径质量辅助证据（空间维度） | Volume Bar |

**附门控**：anchor_freshness（不计入因子数，前置有效性开关）

**窗口参数**（单一基础参数 K 派生）：

```
K = 基础窗口单位（初始建议值：20 根 Volume Bar）

CVD 窗口          = K
PPE 短窗口        = K
OLS 窗口          = 3K（仅确认脱离后的带外 Bar 参与计算）
OLS 最少柱数      = 3K
PPE 历史窗口      = 20K
带内吸收趋势窗口  = K
中心性窗口 (v1.3) = 3K
中心性半衰期(v1.3)= K
```

### 7.1 - 7.8

[因子详细定义在 v1.3 规范文档中完整给出，v1.3.1 未变更。为保持单文档可读性，以下只列出关键计算公式。]

**band_half**（v1.2）：
```
σ_slow      = detrended_std(close[-3K:])
sigma_count = 3 + 3 × tanh(spring_capacity_per_sigma / 5)
band_half   = clamp(σ_slow × sigma_count, min=price×0.1%, max=price×1.5%)
```

**PPE**：
```
PPE = |close - open| / (high - low)
PPE_percentile = value 在过去 20K 根非尖峰 Bar 历史分布中的百分位
                 (v1.3.1 Fix-3: 当前柱不自计入历史分布)
```

**标准化偏离**：
```
normalized_deviation = (price - flip_point) / band_half
```

**OLS**（仅 CONFIRMED 状态的带外 Bar 参与，窗口=3K，最少柱数=3K）：
```
slope, r_squared = OLS(x=[0..n-1], y=normalized_deviation序列)
R² ≥ 0.3: 斜率有效；R² < 0.3: 斜率无效（标注非线性过程）
```

**CVD**（窗口=K）：
```
cvd_sum       = Σ cvd_delta (过去 K 根 Bar)
cvd_strength  = |cvd_sum| / (K × volume_bar_n)
cvd_direction = sign(cvd_sum)
strength < 0.2: 输出 neutral
(v1.3.1 Fix-5: |normalized_deviation| < 0.10 时也输出 neutral)
```

---

## 8. 数据获取方式

[内容在 v1.3 规范中完整给出，v1.3.1 未变更。摘要：使用 Binance aggTrades REST 轮询（FMZ 沙盒约束），通过 trade_id 连续性检测 CVD 降级，降级期间 `cvd_strength` 强制归零但不清空缓冲区。]

---

## 9. 模块化设计

### 9.1 - 9.9 模块职责

[完整内容在 v1.3 规范中给出，v1.3.1 未改变模块划分。七模块：BarAssembler、AnchorContext、DeviationTracker、ClassificationEvidence、SystemStateManager、LabelGenerator、SnapshotRecorder。]

### 9.10 主循环结构

```
每根 Volume Bar 完成时（BarAssembler 触发）：

  Step 1: AnchorContext.check_update()
          如有新 GEX 快照则更新，检测 anchor_shift_event
          计算 band_half（需 BarAssembler 提供 σ_slow）
          检测锚偏移，返回 shift_magnitude（相对量）

  Step 2: DeviationTracker.update(bar)
          更新 normalized_deviation 和带外/带内持续计数
          管理四状态转换（含 REENTRY_PENDING）

  Step 3: SystemStateManager.update()
          读取 Step1、Step2 输出 + T-1 就绪信号缓存
          推进四轴状态，发出重置 / 冻结指令
          (v1.3.1 Fix-6: shift_magnitude 与 anchor_ppe_reset_frac 比较
           以决定是否发出 reset_ppe_history 指令)

  Step 4: ClassificationEvidence.compute(bar, instructions, cvd_degraded)
          先执行 Step3 指令（清空 / 冻结）
          再按当前状态计算因子原始值
          (v1.3.1 Fix-3: ppe_percentile 先算后 append)
          CVD 降级时 strength 强制归零
          更新就绪信号缓存（供下一根 Bar 的 Step3 使用）

  Step 5: LabelGenerator.generate()
          消费 Step4 原始值和 Step3 状态，生成标签
          (v1.3.1 Fix-2: EXPIRED 状态下 anchor_health_score = None)
          (v1.3.1 Fix-5: normalized_deviation 极小时 cvd_label = neutral)

  Step 6: SnapshotRecorder.write()
          EventAssembler 组装快照，分表持久化

  Step 7 (Display, 不在主逻辑职责内):
          事件驱动 Flag 打标
          (v1.3.1 Fix-1: anchor_shift 事件现在也能触发 Flag)

主循环不包含任何业务逻辑，只做顺序调度。
```

---

## 10. 封版补丁细节（v1.0）

[内容在 v1.0 / v1.1 文档中完整给出，v1.3.1 未变更。]

---

## 11. v1.1 修复日志

[内容在 v1.1 文档中完整给出，v1.3.1 未变更。]

---

## 12. v1.2 架构迭代日志

[内容在 v1.2 文档中完整给出，v1.3.1 未变更。]

---

## 13. v1.3 架构迭代日志

[内容在 v1.3 文档中完整给出，v1.3.1 未变更。]

---

## 14. 已知局限声明

以下局限被明确承认，当前版本不试图解决：

**GEX 数据是理论对冲义务**：flip_point 的理论位置正确，但做市商的实际执行可能与理论义务存在偏差。GEX 更新频率是系统精度的硬上限。

**PPE 是 LP 行为的概率性代理**：PPE 低与 LP 积极维护的场景统计相关，但不是直接度量。

**band_half 参数是经验值**：tanh 分母"5"无物理推导来源，σ_slow 计算窗口待校准。

**OLS 在非线性过程下失效**：R² 过滤是部分保护。先扩展后收缩的偏离路径可能产生接近零的斜率。

**CVD 强度门阈值是经验值**：0.2 的初始设定需要实盘数据验证。

**REST 轮询下 CVD 有数据完整性风险**（v1.1 新增）：Binance aggTrades REST 单次上限 1000 条。

**Volume Bar 成交量单位 N 待实测确定**。

**系统不解释所有行情**：classification_state = UNAVAILABLE 或 PARTIAL 时，系统主动标注不确定性，不强行输出分类结论。

**anchor_health_score 是未校准的观测指标**（v1.3 新增）：健康度评分的 sigmoid 参数均为经验初值，未经实盘验证。分档阈值（80/60/40/20）是 UI 便利性设定。v1.3 显式禁止将健康度作为交易门槛或状态机输入使用，直到至少 4 周实盘数据验证其预测力。

**中心性因子在 CONFIRMED 期间冻结**（v1.3 新增）：带外期间"锚的中心性"不再有物理意义，冻结是正确的设计选择。但冻结期可能持续很长，期间健康度的空间因子反映的是冻结前最后时刻的状态，不是实时状态。

**center_loss 使用 RMS 而非 MAE**（v1.3 新增）：RMS 对极端值更敏感。d_cap 截断提供了部分保护。若实盘发现过于敏感，v1.4 可切换。

**中心性冻结期恢复后的 age 语义**（v1.3.1 新增，延后至 v1.4+ 讨论）：中心性因子在 CONFIRMED 冻结后恢复更新时，当前 age 语义仍采用 buffer 回数（即 `age_i = n - 1 - idx`），而非有效观测 age（跳过冻结期的实际连续观测计数）。这意味着若 CONFIRMED 持续了较长时间，冻结前最后一根有效 Bar 在恢复后的第一次重算中仍按"回数"算 age，但其挂钟 age 实际远超 buffer 回数所暗示的值。后果：恢复首柱的 ED1 / center_loss 可能对冻结前的历史值保留较强记忆，不完全是"实时重心"的语义。v1.3 / v1.3.1 不引入 `frozen_bars_skipped` 或 `effective_age` 等新实体，本项明确延后至 v1.4 讨论是否需要拆分 age 定义。

---

## 15. v1.3.1 修补日志

v1.3.1 是 v1.3 的**外科式修补轮**。本轮只处理审查中确定的 bug 与架构未拆开的分层响应，不新增模块、不新增状态轴、不新增事件类型、不改变健康度观测定位、不改动中心性因子的计算规则。

### 15.1 Fix-1：Chart Flag 补齐 `anchor_shift` 分支

**问题**：v1.2 规范和 FLAG_MAP 声称 Flag 覆盖三类离散事件（departure_confirmed / gap_closure / anchor_shift），但 `Display._maybe_add_event_flag` 实际只对前两类做了分支处理，`anchor_shift` 条目是死代码。代码中甚至留有自己的 TODO 注释承认此问题未完成。

**修复**：
- 扩展 `_maybe_add_event_flag` 签名，新增 `anchor_shift_event` 和 `shift_magnitude` 两个参数由调用方显式传入。
- 在函数体内按优先级顺序检测三类事件：`departure_confirmed` > `gap_closure_confirmed` > `anchor_shift`。
- 同一根 Bar 同时发生多类事件时只打一个 Flag（优先级取前者），避免 Flag 叠加。
- 锚迁移的 Flag 文本使用 `shift_magnitude`（相对量）而非分类结论字段，与 Flag 语义一致。
- `anchor_shift` 无需置信度过滤（与 `departure_confirmed` 同属结构性事件）。

**影响范围**：
- 改动模块：`Display`
- 影响状态机：否
- 影响快照字段：否（`anchor_shift_event` 字段早已存在于 `raw_values`，仅消费方补齐）
- 影响状态栏：否（状态栏"高价值事件流"早已覆盖三类事件，本次只影响图表 Flag）

### 15.2 Fix-2：EXPIRED 状态下健康度输出消除伪精度

**问题**：`_compute_anchor_health` 只对 `runtime_gate == "COLD_START"` 做早退，未处理 `anchor_state == "EXPIRED"`。当 GEX 数据超过 `gex_freshness_expired_ms`（60 分钟）时，`H_time` 按分段衰减公式归零，使总分 `≈ 0`、等级 `"濒危"`。但 EXPIRED 的物理含义是"锚参照系本身已不可信"——此时 ED1 / center_loss 等中心性因子都是基于过期的 `flip_point` 计算出的。继续输出具体分数是**测量失效伪装成测量危险**，误导观察者。

**修复**：`_compute_anchor_health` 新增一条早退：

```
if anchor_state == "EXPIRED":
    return None, "未就绪", None
```

状态栏 `_build_health_table` 对 `score == None` 的处理也相应加强：不再统一显示 `-`，而是显示 `level` 文本（`"未就绪"`），避免被误解为数据缺失。

**影响范围**：
- 改动模块：`LabelGenerator`（主修复）、`Display`（状态栏展示配合）
- 影响状态机：否
- 影响快照字段：否（`anchor_health_score` 允许为 None 的契约在 v1.3 已定义）
- 影响状态栏：是（EXPIRED 时从显示"0.0 / 濒危"改为显示"未就绪"）

**与 COLD_START 的一致性**：两种情形都意味着"当前不具备计算健康度的前提条件"，语义上等价。COLD_START 是还没准备好，EXPIRED 是已经失效——都对应"无法给出有意义的健康度读数"。

### 15.3 Fix-3：`ppe_percentile` 先算后 append

**问题**：`ClassificationEvidence.compute()` 原实现顺序为先 `self._ppe_history.append(ppe_raw)`、后 `percentile_rank(ppe_raw, history_list)`。这导致当前样本被计入自己的历史分布参照系，产生 `1/n` 的"自吹自擂"偏差。窗口满时（`n = 20K = 400`）偏差 ≈ 0.25% 可忽略，但**冷启动期（窗口仅几十根）偏差显著**——当前 PPE 若为历史最高，排名会比"它不自计入"的场景高出数个百分点。

**修复**：交换顺序，`percentile_rank` 在 append 之前调用：

```python
ppe_history_snapshot = list(self._ppe_history)  # 快照: 不含当前样本
ppe_percentile       = percentile_rank(ppe_raw, ppe_history_snapshot)

if ppe_raw is not None and not ppe_is_spike:
    self._ppe_history.append(ppe_raw)
    ...
```

尖峰 Bar 的 `ppe_raw` 本就不进入 `_ppe_history`，但会在快照表 `ppe_raw` 字段保留；百分位计算对尖峰也使用修复后的逻辑（对应非尖峰的历史分布做排名）。

**影响范围**：
- 改动模块：`ClassificationEvidence`
- 影响状态机：否
- 影响快照字段：否（字段含义与名称均未变，仅计算顺序修正）
- 影响状态栏：否

### 15.4 Fix-4：`_build_health_table` 注释清理

**问题**：`Display._build_health_table` 内有一段陈旧注释描述"状态栏这里退化为只展示总分，第一版简化：展示不重算"。该注释来自 v1.3 早期原型，但最终实现已经通过 `labels["anchor_health_breakdown"]` 从 `LabelGenerator._compute_anchor_health` 直接获取了四因子分解并展示。注释-实现脱节会误导后续维护者以为分解"其实没在展示"。

**修复**：重写该段注释，明确说明 `breakdown` 已由 `labels` 副产出字段传入，状态栏只做渲染不重算。

**影响范围**：纯注释清理，无代码行为变化。

### 15.5 Fix-5：`_make_cvd_label` 零偏离返回 neutral

**问题**：原实现 `deviation_sign = 1 if normalized_deviation > 0 else -1`，在 `normalized_deviation == 0` 时静默落入 `-1` 分支，产生错误的 `"same" / "opposite"` 标签。理论上价格正好等于 `flip_point` 时没有方向可言；实盘中严格的 `== 0` 极少发生，但同样量级的浮点抖动（如 `±1e-9σ`）同样是没有方向语义的。

**修复**：在 `cvd_strength` 闸门和 `normalized_deviation == None` 检查之后，新增一道 deadzone 判断：

```python
if abs(normalized_deviation) < CONFIG["centrality_sign_eps"]:
    return "neutral"
```

阈值复用 `centrality_sign_eps`（默认 0.10）。复用的语义依据：这是系统已经确立的"视为中性"的方向死区阈值，与中心性因子的 `sign_eps(d)` 计算使用同一常数，避免新增独立配置项增加耦合面。

**影响范围**：
- 改动模块：`LabelGenerator`
- 影响状态机：否
- 影响快照字段：否（`cvd_label` 字段含义不变）
- 影响状态栏：否（标签映射表不变，仅更精细地分配 neutral 分支）

**与分类矩阵的一致性**：cvd_label == "neutral" 本就是分类矩阵的合法输入（对 `(contraction, neutral)`、`(oscillation, neutral)` 等有明确映射），新增的 deadzone 触发不会破坏矩阵结构。

### 15.6 Fix-6：分层阈值拆分 `anchor_shift_frac` 与 `anchor_ppe_reset_frac`

**背景**：v1.3 规范 §8.7 中 `reset_ppe_history` 的触发条件写作 "anchor_shift_event = True 且 shift_magnitude > 0.5"。然而代码实现中，`anchor_shift_event` 本身的触发阈值也是 `CONFIG["anchor_shift_frac"] = 0.5`。两者绑在同一阈值上意味着：**只要 shift_event 发生，magnitude 必然已经 > 0.5**，于是 "额外判断 > 0.5" 的分层条件永远为 True，两档响应坍缩为一档——任何锚位移都会清空 20K 根 PPE 历史（≈ 5-6 小时数据）。

**重要说明**：这不是"代码偏离规范"，而是"v1.3 规范和实现都还没把两档响应拆开"。本 Fix 是**架构补全**，不是 bug 回填。

**修复**：
1. CONFIG 新增 `anchor_ppe_reset_frac`（默认 `1.0`）：
   ```python
   "anchor_shift_frac":     0.5,   # 轻档：触发 RESETTING + OLS + deviation 重置
   "anchor_ppe_reset_frac": 1.0,   # 重档：额外触发 PPE 历史重置
   ```

2. 硬约束 `anchor_ppe_reset_frac >= anchor_shift_frac`，由模块加载时 `validate_config()` 强制检查，违反直接 raise ValueError。一同检查的还有 `health_level_*` 四档阈值的单调性。

3. `SystemStateManager.update()` 使用新阈值：
   ```python
   if anchor_shift_event:
       # 轻档：必然执行
       instructions["reset_ols_window"]        = True
       instructions["reset_deviation_counter"] = True
       # 重档：仅在更大位移时执行
       if shift_magnitude > CONFIG["anchor_ppe_reset_frac"]:
           instructions["reset_ppe_history"] = True
   ```

**物理动机**：
- 轻档（shift_magnitude ∈ (0.5, 1.0]）：锚在带宽的一半范围内移动。这个级别的迁移足够触发新的 OLS 窗口和 deviation 计数重置，但 PPE 的历史分布仍然描述的是相近锚环境下的路径质量分布，保留更有价值。
- 重档（shift_magnitude > 1.0）：锚位移超过一整个带宽半径，原有 PPE 历史分布的参照系已经不再合理，清空重新积累。

**影响范围**：
- 改动模块：CONFIG（新增项 + 新增 `validate_config()`）、`SystemStateManager`
- 影响状态机：否（状态转换规则未变，只改变 `reset_ppe_history` 指令的触发条件）
- 影响快照字段：否（`reset_ppe_history` 字段含义不变，仅触发频率降低）
- 影响状态栏：否

**后向兼容**：若用户在 CONFIG 中手动把 `anchor_ppe_reset_frac` 设回等于 `anchor_shift_frac`（0.5），行为退化为 v1.3 的坍缩版本（任何 shift 都清空 PPE 历史）。本 Fix 的默认值 `1.0` 拉开了分层，但允许配置回退。

---

## 不在本轮修复的项（明确延后）

以下问题在审查中被识别，但按裁决明确延后到 v1.4 或更后的版本，不在 v1.3.1 范围内。

### L-1 中心性冻结期恢复后的 age 语义

**现状**：`ClassificationEvidence._compute_centrality_factors()` 使用 `age_i = n - 1 - idx`（buffer 中回数）计算指数权重。`CONFIRMED` 冻结期间 `_centrality_buffer` 不写入，恢复更新后 buffer 最新位置写入新值，但其他位置仍是**冻结前的老值**——这些老值的 "buffer 回数 age" 很小（因为它们在 buffer 里物理位置仍然靠后），但"实际观测 age"已经拉长到冻结时长 + buffer 回数。

**后果**：恢复首柱重算 ED1 / center_loss / sign_consistency 时，冻结前的最后一批老值权重偏高，使恢复后的中心性读数对"冻结时刻的重心"保留较强记忆，不完全是"实时重心"语义。

**为什么不在 v1.3.1 修**：
- 这不是"实现偏离了规范"，v1.3 规范和代码都一致地采用了"buffer 回数 age"。
- 修改需要引入 `frozen_bars_skipped` 或 `effective_age` 等新实体，改变 age 的定义本身。
- 这属于"模型语义待定"，需要更深的讨论判断：恢复后的中心性是应该"完全遗忘冻结期发生的一切"，还是应该"把冻结期按挂钟 age 计入衰减"，抑或是"冻结期数据完全跳过"。三个选择对应不同的物理假设。

**延后去向**：v1.4 讨论是否拆分 age 定义，本项同时写入 §14 已知局限。

### L-2 `erosion_drift` 与 `anchor_health_score` 的实盘预测力校准

v1.3 中所有健康度 sigmoid 参数（inflection、beta）都是经验初值。v1.3.1 不引入实盘校准，继续保留原设定。校准工作需要至少 4 周实盘数据，明确延后到 v1.4。

### L-3 `band_half` 的 `tanh` 分母参数 "5" 的物理推导

继承自 v1.1 / v1.2 已知局限，v1.3.1 未处理。

---

*文档完。v1.3.1 外科式修补版。*
