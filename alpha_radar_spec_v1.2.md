# Alpha Radar — 项目设计规范文档

> 版本：v1.2  
> 前版：v1.1（前前版：封版 v1.0）  
> 性质：研究型观测系统，非交易执行系统

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
13. [已知局限声明](#13-已知局限声明)

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

**窗口参数**：全部由单一基础参数 K 派生，K 是系统唯一需要实盘校准的核心参数。

```
K = 基础窗口单位（初始建议值：20 根 Volume Bar）

CVD 窗口          = K
PPE 短窗口        = K
OLS 窗口          = 3K（仅确认脱离后的带外 Bar 参与计算）
OLS 最少柱数      = 3K（v1.1 修正：与窗口一致，不允许提前计算）
PPE 历史窗口      = 20K
带内吸收趋势窗口  = K（带内 Bar 短窗中位数）
```

---

### 7.1 flip_point

**物理含义**：做市商 delta 对冲方向发生反转的价格水平。在此价格以上，做市商对冲行为倾向于卖出现货；在此价格以下，倾向于买入现货。这个对冲义务的方向切换点天然构成对价格的双向约束，是定价锚的位置定义。

**来源**：GexMonitor REST API 直接取值，不做任何加工。

**更新触发**：GEX 快照更新时同步更新。flip_point 位移超过 `0.5 × band_half` 时，触发 anchor_shift_event，下游模块响应重置。

**已知局限**：GEX 数据描述的是理论对冲义务，不是实际执行行为。flip_point 的理论位置正确，但做市商的实际对冲行为可能与理论义务存在偏差。

---

### 7.2 band_half

**物理含义**：吸收带的半宽度。带宽由两个量共同决定：σ_slow 反映当前局部波动率水平，spring 容量反映做市商在单位波动上能承受的对冲压力。带宽越宽说明做市商弹性越强，带宽越窄说明锚越脆弱。

**计算公式**（继承自 Gamma Spatial Observer v6，v1.2 修订）：

```
σ_slow      = detrended_std(close[-3K:])       # v1.2: 去趋势标准差
sigma_count = 3 + 3 × tanh(spring_capacity_per_sigma / 5)
raw_band    = σ_slow × sigma_count
band_half   = clamp(raw_band, min=price×0.1%, max=price×1.5%)   # v1.2 双向护栏
```

sigma_count 范围自适应在 [3, 6] 之间。

**v1.2 关键修订**：σ_slow 从普通标准差改为**去趋势标准差**。对 Volume Bar close 序列做线性拟合，用残差的标准差代替原始标准差。物理意义：σ_slow 度量"在当前趋势之上还剩多少震荡"，趋势本身不计入波动率。

**为什么要去趋势**：当价格呈单调趋势时，原始 std 反映的是**方向性位移**而非**震荡幅度**，会导致：

```
单边快速上涨 → close 序列单调 → 原 std 暴涨
             → band_half 跟着暴涨
             → normalized_deviation 被压小
             → 带宽把偏离"吸收"回去（指标自吞噬）
```

去趋势后：震荡行情下拟合斜率 ≈ 0，残差 ≈ 原始偏差，公式退化为 v1.1 行为；趋势行情下拟合带走方向分量，残差保持合理幅度，band_half 稳定。

**v1.2 工程护栏**：在去趋势标准差的基础上再叠加相对价格硬上限 `band_half ≤ price × 1.5%`。本护栏作为 last-resort 兜底，仅在去趋势后仍异常的极端情况触发（例如数据污染、GEX spring 异常高）。触发时 `_band_clamped = True`，写入 raw_values 供审计，同时在状态栏 [CLAMP] 标注。

**参数标注**：tanh 分母"5"是经验设定值，无物理推导来源。σ_slow 的去趋势窗口（3K）和 `band_half_max_pct`（1.5%）是待校准参数。

**使用方式**：吸收带定义为 `[flip_point - band_half, flip_point + band_half]`，作为均衡内扰动和有效脱离的空间边界。

---

### 7.3 PPE（Price Path Efficiency，价格路径效率）

**物理含义**：这根 Volume Bar 内，价格净位移占价格总行程的比例。PPE 接近 1 意味着价格直线运动，单边畅通，吸收力弱。PPE 接近 0 意味着价格大量来回震荡但净位移很小，双向对抗存在，吸收在发生。

**计算公式**：

```
PPE = |close - open| / (high - low)
```

范围 [0, 1]，每根 Volume Bar 独立计算。

**表述边界**：PPE 低与 LP 积极维护的场景统计相关，但不是 LP 行为的直接度量。两个大方向盘反复博弈在锚附近同样会产生低 PPE 读数。这个局限在没有订单簿数据的前提下无法完全消除，是已知盲区。

**针刺保护**：当 `high - low > 1.5 × band_half` 时，该 Bar 的 PPE 标记为异常（is_spike = True），不参与 PPE 历史分布更新，但**保留原始值记录**供快照回溯。v1.1 修正：尖峰 Bar 的 ppe_raw 字段在快照中有值而非 None，ppe_is_spike 字段标注异常状态。

**在锚判定层的使用**（百分位，相对判断）：

```
PPE_percentile = 当前 PPE 在过去 20K 根 Bar 历史分布中的百分位

PPE_percentile < 0.3：吸收强，锚效用获得流动性支撑
PPE_percentile > 0.7：吸收弱，锚效用缺乏流动性支撑
0.3 ≤ PPE_percentile ≤ 0.7：中性，不单独表态
```

**在分类判定层的使用**（原始值，绝对判断）：

使用 PPE 原始值作为空间维度的吸收状态辅助证据，定位为辅助项，不作硬门槛。

```
ppe_raw < 0.3  → "高阻力"（这根 Bar 有强吸收）
ppe_raw > 0.7  → "低阻力"（这根 Bar 畅通无阻力）
其他           → "中性"
```

v1.1 修正：锚判定层使用百分位（历史相对）、分类判定层使用原始值（当前绝对），两层各有独立阈值配置，不混用。

---

### 7.4 带内吸收趋势标签

**语义定义**：带内吸收状态是否在趋势性增强、走弱或维持稳定。这不是机械的 PPE 数学斜率，而是对带内吸收质量趋势的描述性标签。

**实现方式**：对带内 Volume Bar 的 PPE 短窗（K 根）中位数序列进行平滑后判断趋势方向，而非直接对原始 PPE 序列做 OLS 回归（原始 PPE 噪声过重，分布漂移影响大）。

**计算限制**：

- **仅在带内计算**：价格进入带外确认状态后，本标签立即冻结，不再更新。
- **带外冻结，不降权**：冻结期间标签保留最后的带内状态值，但标注"冻结中"。
- **价格重新回到带内时**：标签解冻，从当前 Bar 开始重新积累带内序列，窗口重置。

**输出标签**（三值）：

```
锚承压中：PPE 中位数趋势性升高（斜率 > 0），吸收强度递减
锚状态稳定：PPE 中位数无明显趋势
锚修复中：PPE 中位数趋势性降低（斜率 < 0），吸收强度增强
```

**物理方向映射**（v1.1 显式标注）：

PPE 升高 = 路径效率升高 = 价格走得更直 = 吸收力减弱 = **锚承压**。PPE 降低 = 路径效率降低 = 价格来回震荡 = 吸收力增强 = **锚修复**。斜率的符号与标签方向的对应关系是：正斜率 → 承压，负斜率 → 修复。

**在框架中的角色**：服务于锚判定层的效用强度描述，作为 PPE 百分位的趋势补充标签。不参与带外的任何判断，不影响事件二的分类结论。

---

### 7.5 标准化偏离

**物理含义**：当前价格相对于定价锚的偏离，以吸收带半宽度为单位表达。无量纲，在 BTC 不同绝对价格水平下可比，在不同波动率环境下也可比（因为 band_half 本身包含 σ_slow）。

**计算公式**：

```
normalized_deviation = (price - flip_point) / band_half
```

正值：价格在 flip_point 上方。负值：价格在 flip_point 下方。方向信息保留，不取绝对值。

**使用规则**：

```
|normalized_deviation| < 1：带内，均衡内扰动候选
|normalized_deviation| ≥ 1：带外，进入带外持续 Bar 计数
```

---

### 7.6 带外持续 Bar 计数

**物理含义**：价格在吸收带外连续存在的 Volume Bar 数量。这个量的存在是为了区分真实脱离和针刺噪声。

**计算规则**：

- 每根带外 Bar 计数 +1。
- 价格重新回到带内时，计数清零。
- 锚重置事件（anchor_shift_event）触发时，计数清零。

**确认阈值**：

```
带外持续 Bar 计数 < 2：候选脱离，不进入分类层
带外持续 Bar 计数 ≥ 2：确认脱离，进入分类层
```

阈值 2 是初始设定，标注为待校准参数。

**回补确认阈值**（v1.1 新增）：

```
确认脱离后价格回到带内：
  带内持续 Bar 计数 < 2：回补待确认（REENTRY_PENDING），分类层保持输出
  带内持续 Bar 计数 ≥ 2：回补确认，状态回 INSIDE，触发 gap_closure_event
  回补待确认期间价格再次出带：直接恢复 CONFIRMED，无需重走 CANDIDATE
```

阈值 2 与脱离确认对称，标注为待校准参数。

**与 Gamma Observer 的继承关系**：此逻辑与 Gamma Observer 里的 2-tick breakout 确认机制同构，用 Volume Bar 替代了 tick 计数，物理动机相同。

---

### 7.7 OLS 斜率（R² 强绑定）

**物理含义**：每过一根 Volume Bar，标准化偏离平均变化多少。直接回答"偏离是否在持续扩展或收缩"的时间维度问题。

**计算公式**：

```
y = [normalized_deviation_0, ..., normalized_deviation_{3K-1}]
x = [0, 1, 2, ..., 3K-1]
slope, intercept, r_squared = OLS(x, y)
```

**R² 强绑定规则**：OLS 斜率和 R² 是强绑定对，任何消费方必须同时读取两者，不允许只读 slope。

```
R² ≥ 0.3：斜率有效，输出斜率值
R² < 0.3：斜率无效，输出空值，标注"非线性过程"
```

R² 门限 0.3 标注为待校准参数。

**计算窗口限定**（v1.1 修正）：

- 仅使用**确认脱离后（event_state = CONFIRMED）的带外 Bar** 参与计算。CANDIDATE 阶段的首根 outside bar 和 REENTRY_PENDING 阶段的带内 bar 不参与。
- OLS 最少柱数 = 3K（与窗口大小一致），不允许在窗口未满时提前计算。
- 价格回补确认后（gap_closure_event），窗口清空，斜率输出空值。
- anchor_shift_event 触发时，窗口强制重置。

**已知边界条件**：当偏离序列先扩展后收缩（或反之）时，OLS 斜率会输出接近零的值，与"偏离震荡"的读数相同但含义不同。R² 过滤是部分保护，不是完全保护。非线性过程在 R² 过滤后标注为"非线性过程"，不强行输出分类结论。

---

### 7.8 CVD 方向（强度门）

**物理含义**：在当前窗口内，成交力量是否在持续朝某一方向积累，以及这种积累的强度。CVD 方向强且与价格偏离方向一致，说明脱离被真实成交支撑。

**计算方式**：

```
cvd_delta（每根 Bar）= 买方 Taker 成交量 - 卖方 Taker 成交量

CVD_direction = sign(Σ cvd_delta，过去 K 根 Bar)
CVD_strength  = |Σ cvd_delta| / (K × volume_bar_n)
```

CVD_strength 归一化到 [0, 1]。

**强度门规则**：

```
CVD_strength < 0.2：输出"中性"，无论方向符号如何
CVD_strength ≥ 0.2 且方向与偏离一致：输出"同向"
CVD_strength ≥ 0.2 且方向与偏离相反：输出"反向"
```

强度门阈值 0.2 标注为待校准参数。

**内部实现约束**：CVD 必须同时维护 direction 和 strength 两个字段，不允许只输出方向符号。外部标签展示为三值（同向 / 反向 / 中性），内部原始值必须保留 cvd_direction 和 cvd_strength 供回溯。

**数据来源与完整性保障**（v1.1 修订）：

当前实现使用 Binance aggTrades REST 轮询（详见第 8 节）。REST 单次上限 1000 条，高流速下可能丢失成交。系统通过 trade_id 连续性检测缺口，缺口发生时 CVD 进入降级状态（cvd_strength 强制归零，标签输出 neutral），下一次无缺口的完整轮询自动恢复。降级期间 CVD 缓冲区继续正常写入（纪律 3：不清空历史），仅输出层做保护性截断。

---

## 8. 数据获取方式

### 8.1 设计选择与工程约束

v1.0 规范在概念层将成交数据来源描述为"WebSocket aggTrades 流"。当前落地实现运行在 FMZ 平台沙盒环境，该环境不支持长连接 WebSocket，因此实际使用 **Binance aggTrades REST API** 轮询获取成交数据。

这一替换是 FMZ 平台约束下的工程妥协，不是设计层面的选择。当运行环境支持 WebSocket 时，应优先切换回 WebSocket 方式以获得完整的成交流。

### 8.2 两条数据管道

| 数据 | 获取方式 | 端点 | 轮询/推送 | 节流 |
|---|---|---|---|---|
| GEX 快照 | REST GET | gexmonitor.com/api/gex-latest | 轮询 | 60 秒最小间隔 |
| aggTrades 成交流 | REST GET | api.binance.com/api/v3/aggTrades | 轮询 | 每 tick（2 秒） |

### 8.3 REST aggTrades 的工作方式

每次轮询使用 `fromId = last_trade_id + 1`，最多返回 1000 条。BarAssembler 逐笔消费，按 `volume_bar_n` BTC 切割为等币量柱。

**等币量柱的完整性**：REST 返回的每笔成交带有完整的 trade_id、价格、数量、aggressor side，与 WebSocket 逐笔推送在语义上等价。只要 trade_id 连续无缺口，切出的 Volume Bar 在 OHLC 和 cvd_delta 上与 WebSocket 完全一致。

**Trade ID 断层处理**（纪律 3）：发现 trade_id 不连续时，仅打 WARN 日志，绝不清空任何已积累的历史窗口。历史波动率基线是宝贵的，不能因网络抖动而丢弃。

### 8.4 REST 对各因子的影响评估

| 因子 | REST 影响 | 说明 |
|---|---|---|
| Volume Bar OHLC | 无 | trade_id 连续时与 WebSocket 等价 |
| PPE | 无 | 仅依赖单根 Bar 的 OHLC |
| OLS 斜率 | 无 | 仅依赖带外 Bar 序列的 normalized_deviation |
| σ_slow / band_half | 无 | 仅依赖 Volume Bar close 序列的标准差 |
| **CVD** | **有风险** | 缺口期间丢失的成交导致 CVD 偏差 |

### 8.5 CVD 降级机制（v1.1 新增）

**问题本质**：REST 单次返回上限 1000 条。若两次轮询间隔内成交笔数超过 1000（BTC 剧烈行情下可能发生），中间的 trade 被跳过，CVD 的方向和强度都会失真。

**降级方案**：

```
检测到 trade_id 缺口：
  → cvd_degraded = True
  → CVD strength 在 ClassificationEvidence 输出层强制归零
  → LabelGenerator 因 strength < gate 自然输出 "中性"
  → 分类矩阵走中性分支，不被脏 CVD 数据误导

下一次无缺口的完整 poll：
  → cvd_degraded = False，自动恢复

不做的事（纪律 3）：
  × 不清空 CVD 缓冲区
  × 不清空任何其他历史窗口
  × 不尝试回补丢失的成交（REST 按 fromId 拉取，无法回填）
```

**降级状态可观测**：raw_values 快照中记录 `cvd_degraded` 布尔字段，回放时可追溯哪些柱的 CVD 处于降级状态。

---

## 9. 模块化设计

### 9.1 设计原则

模块化的目标是解决复杂系统在实盘调试时的定位困难问题。设计遵循以下原则：

- **状态集中，缓存分治**：所有状态切换规则集中在 SystemStateManager，各模块维护各自内部缓存，接收状态机指令后执行对应的清空或冻结动作。
- **原始值与标签分离**：所有因子先输出原始值，LabelGenerator 统一做原始值到标签的映射，阈值集中定义，校准时只改一处。
- **数据管道独立可监控**：数据质量问题（断线、缺口、CVD 降级）与因子计算问题必须能独立定位，不互相污染。
- **主循环只做调度**：主循环不包含任何业务逻辑，只做顺序调度。

### 9.2 7 模块结构总览

```
BarAssembler           数据管道层（含 CVD 降级状态追踪）
AnchorContext          锚计算层
DeviationTracker       脱离计算层（含 REENTRY_PENDING 状态）
ClassificationEvidence 分类证据层
SystemStateManager     状态机层
LabelGenerator         标签生成层
SnapshotRecorder       快照存储层
```

---

### 9.3 模块一：BarAssembler

**职责**：将 Binance aggTrades REST 响应实时聚合成 Volume Bar。追踪 trade_id 连续性，维护 CVD 降级状态。

**输入**：Binance aggTrades REST 响应（逐笔成交：价格、成交量、aggressor side、trade_id）

**输出**：

```python
{
  open:          float,   # Bar 开盘价
  high:          float,   # Bar 最高价
  low:           float,   # Bar 最低价
  close:         float,   # Bar 收盘价
  total_volume:  float,   # Bar 内总成交量
  cvd_delta:     float,   # 买方 Taker 量 - 卖方 Taker 量
  bar_index:     int,     # 全局 Bar 序号，单调递增
}
```

**CVD 降级状态**（v1.1 新增）：

```python
is_cvd_degraded() → bool
  True:  当前 poll 或最近一次 poll 检测到 trade_id 缺口，CVD 不可信
  False: 最近一次 poll 无缺口，CVD 可信
```

**约束**：只做聚合和数据质量追踪，不做任何计算或判断。Volume Bar 成交量阈值 N 是这里唯一的业务配置参数，标注为待实测校准。

**独立的理由**：REST 轮询延迟、trade_id 断层、Bar 边界对齐等问题必须能单独测试和监控。把数据管道埋在其他模块里，数据质量问题会被误认为因子计算问题。

---

### 9.4 模块二：AnchorContext

**职责**：从 GEX 快照计算锚的位置和宽度，输出门控状态。

**输入**：GEX 快照（flip_point、spring 容量、σ_slow）、上次 GEX 更新时间戳

**输出**：

```python
{
  flip_point:           float,  # 锚位置
  band_half:            float,  # 吸收带半宽度
  anchor_freshness:     str,    # FRESH / STALE / EXPIRED
  anchor_shift_event:   bool,   # 本次更新是否触发锚迁移
  shift_magnitude:      float   # 本次 flip_point 位移 / band_half（无量纲相对量）
}
```

v1.1 修正：shift_magnitude 返回相对量（|位移| / band_half），不是绝对美元位移。这样 SystemStateManager 的 `> 0.5` 判定和日志解释与规范定义一致（0.5 = 半个带宽）。

**anchor_freshness 判定规则**：

```
FRESH：GEX 数据更新间隔 < 3 分钟
STALE：3 分钟 ≤ 间隔 < 60 分钟（置信度降级但不阻断）
EXPIRED：间隔 ≥ 60 分钟，坐标系不可信，下游全部降级
```

**约束**：只处理 GEX 层，不碰成交数据，不输出任何效用判断或标签。锚迁移事件在这里检测，但响应逻辑（触发下游重置）在 SystemStateManager 里处理。

---

### 9.5 模块三：DeviationTracker

**职责**：计算标准化偏离和脱离确认状态。

**输入**：当前 Volume Bar（close 价格）、flip_point、band_half

**输出**：

```python
{
  event_state:          str,    # INSIDE / CANDIDATE / CONFIRMED / REENTRY_PENDING
  normalized_deviation: float,  # (price - flip_point) / band_half
  outside_bar_count:    int,    # 当前带外持续 Bar 数量
  inside_bar_count:     int,    # 回补待确认期间的带内持续 Bar 数量
  deviation_confirmed:  bool,   # event_state == CONFIRMED
  gap_closure_event:    bool,   # 本柱是否触发回补确认
}
```

**四状态转换规则**（v1.1 修订）：

```
INSIDE → CANDIDATE：
  |normalized_deviation| ≥ 1，首根带外 Bar

CANDIDATE → CONFIRMED：
  outside_bar_count ≥ 2

CANDIDATE → INSIDE：
  价格在确认前回到带内（静默重置）

CONFIRMED → REENTRY_PENDING：
  价格回到带内，inside_bar_count = 1

REENTRY_PENDING → INSIDE：
  inside_bar_count ≥ 2（回补确认，触发 gap_closure_event）

REENTRY_PENDING → CONFIRMED：
  价格再次出带（回带失败，直接恢复确认状态，不重走 CANDIDATE）
```

**重置机制**：
- schedule_reset()：由 SystemStateManager 在锚偏移时调用，下一根柱的 update() 开始时生效（one-bar lag 已知且可接受）。

**约束**：只回答有没有脱离、是否连续确认、是否回补确认，不碰 CVD、OLS、PPE 的任何值。

---

### 9.6 模块四：ClassificationEvidence

**职责**：计算全部因子原始值，输出分类层所需的证据数据。

**输入**：Volume Bar 序列、当前空间状态（来自 SystemStateManager）、SystemStateManager 发出的重置/冻结指令、CVD 降级标记

**输出**：

```python
{
  # 锚层 PPE
  ppe_raw:              float,        # 当前 Bar PPE 原始值（尖峰时仍有值）
  ppe_is_spike:         bool,         # 是否为尖峰异常 Bar
  ppe_percentile:       float,        # 历史 20K 窗口百分位
  ppe_short_median:     float,        # 带内 K 窗口中位数（趋势标签上游）

  # 分类层证据
  ols_slope:            float | None, # OLS 斜率（R² 不足时为 None）
  r_squared:            float,        # 与 ols_slope 强绑定
  cvd_direction:        int,          # 原始方向符号（+1 / -1 / 0）
  cvd_strength:         float,        # 归一化强度 [0, 1]（降级时为 0）
  cvd_degraded:         bool,         # CVD 是否处于降级状态
  absorption_frozen:    bool,         # 吸收趋势是否冻结中（供 LabelGenerator 输出"冻结中"）

  # 就绪信号（供 SystemStateManager 消费）
  ppe_history_ready:    bool,         # PPE 历史窗口 Bar 数 ≥ 20K
  ols_window_ready:     bool,         # OLS 窗口带外 Bar 数 ≥ 3K
  r_squared_available:  bool          # 当前 R² ≥ 0.3
}
```

**接收指令及执行动作**：

```
reset_ols_window = True：
  → 清空 OLS 带外 Bar 序列
  → ols_window_ready = False
  → r_squared_available = False

freeze_absorption_trend = True：
  → 冻结 ppe_short_median 序列，停止更新，标注"冻结中"

reset_ppe_short_buffer = True（v1.1 新增）：
  → 清空 ppe_short_median 短窗口
  → 从当前 Bar 重新积累带内序列

reset_ppe_history = True：
  → 清空 PPE 历史窗口
  → ppe_history_ready = False
```

**OLS 计算限制**（v1.1 修正）：

- 仅 **CONFIRMED** 状态的带外 Bar 参与。CANDIDATE 和 REENTRY_PENDING 不参与。
- OLS 最少柱数 = 3K，与窗口大小一致，不允许提前计算。
- 带内 Bar 进入 ppe_short_median 序列，带外时冻结该序列。
- ols_slope 和 r_squared 必须作为绑定对输出，不可分离。

**PPE 尖峰处理**（v1.1 修正）：

- 尖峰 Bar（振幅 > 1.5 × band_half）计算 PPE 原始值并写入快照（ppe_raw 有值），但不进入 _ppe_history 和 _ppe_short_buffer（ppe_is_spike = True）。

**CVD 降级处理**（v1.1 新增）：

- cvd_degraded = True 时，cvd_strength 在输出层强制归零。方向保留但因 strength < gate，LabelGenerator 自然输出 neutral。

**约束**：只产出原始证据值，不产出任何标签，不产出"缺口 / 迁移"结论。

---

### 9.7 模块五：SystemStateManager

**职责**：维护四轴状态机，决定当前哪些因子有效、哪些冻结、哪些降级，并发出重置 / 冻结指令。

**输入**：

```
来自 AnchorContext：    anchor_freshness, anchor_shift_event, shift_magnitude
来自 DeviationTracker：normalized_deviation, outside_bar_count, deviation_confirmed
就绪信号（T-1 缓存）：  ppe_history_ready, ols_window_ready,
                        r_squared_available, deviation_confirmed
```

**就绪信号使用 T-1 缓存值**：就绪信号由 ClassificationEvidence 在每根 Bar 结束后更新并缓存，SystemStateManager 在下一根 Bar 的 Step 3 里读取缓存值，避免 Step 3 和 Step 4 的循环依赖。

**四轴状态定义**（v1.1 修订）：

```python
runtime_gate:         "COLD_START" | "READY"
anchor_state:         "FRESH" | "STALE" | "EXPIRED" | "RESETTING"
event_state:          "INSIDE" | "CANDIDATE" | "CONFIRMED" | "REENTRY_PENDING"
classification_state: "UNAVAILABLE" | "PARTIAL" | "AVAILABLE"
```

**四轴独立转换规则**：

runtime_gate：
```
COLD_START → READY：ppe_history_ready = True
READY → COLD_START：系统重启
```

anchor_state：
```
FRESH / STALE / EXPIRED：由 anchor_freshness 直接映射
任意 → RESETTING：anchor_shift_event = True 且 shift_magnitude > 0.5
RESETTING → FRESH：GEX 新快照接收且锚位置稳定（连续 K 根 Bar 无大幅位移）
```

event_state：
```
镜像 DeviationTracker 输出（INSIDE / CANDIDATE / CONFIRMED / REENTRY_PENDING）
```

classification_state（v1.1 修正）：
```
UNAVAILABLE：
  runtime_gate = COLD_START
  或 anchor_state IN [EXPIRED, RESETTING]
  或 event_state ∉ [CONFIRMED, REENTRY_PENDING]
  或 ppe_history_ready = False

PARTIAL：
  event_state IN [CONFIRMED, REENTRY_PENDING]
  且 anchor_state IN [FRESH, STALE]
  且 ppe_history_ready = True
  且 (ols_window_ready = False 或 r_squared_available = False)
  注: anchor_state = STALE 也只能到 PARTIAL，不能到 AVAILABLE

AVAILABLE：
  event_state IN [CONFIRMED, REENTRY_PENDING]
  且 anchor_state = FRESH               (v1.1: 严格要求 FRESH)
  且 ppe_history_ready = True
  且 ols_window_ready = True             (v1.1: 3K 根带外柱)
  且 r_squared_available = True           (v1.1: 必须同时检查 R²)
```

**发出的指令集**（v1.1 修订）：

```python
{
  reset_ols_window:        bool,  # True 当 anchor_shift_event = True
                                  #       或 gap_closure_event = True
  freeze_absorption_trend: bool,  # True 当 event_state ∉ [INSIDE]
  reset_deviation_counter: bool,  # True 当 anchor_state = RESETTING
  reset_ppe_history:       bool,  # True 当 anchor_shift_event = True
                                  #       且 shift_magnitude > 0.5
  reset_ppe_short_buffer:  bool,  # True 当 event_state 从非 INSIDE 转为 INSIDE
}
```

**约束**：SystemStateManager 是唯一可以修改四轴状态的模块。其他模块只读取状态，不写入状态。状态机只发出指令，不直接持有各模块的内部缓存窗口。

---

### 9.8 模块六：LabelGenerator

**职责**：消费 ClassificationEvidence 的原始值和 SystemStateManager 的四轴状态，生成所有标签。

**输入**：ClassificationEvidence 全部原始值、SystemStateManager 四轴状态 + 指令

**输出**：

```python
{
  # 锚层标签
  absorption_trend_tag: str,  # 锚承压中 / 锚状态稳定 / 锚修复中 / 冻结中 / 锚状态未知
  anchor_validity:      str,  # 有效 / 轻微延迟 / 已过期 / 重置中

  # 分类层标签
  ols_label:            str,  # 扩展 / 收缩 / 震荡 / 无效
  cvd_label:            str,  # 同向 / 反向 / 中性
  ppe_quality:          str   # 高阻力 / 中性 / 低阻力 / 未知

  # 分类结论
  classification_result: str | None,  # 定价中心迁移 / 暂时性缺口 / 可恢复缺口 / ...
  confidence:            str | None,  # HIGH / MEDIUM / LOW / PARTIAL
}
```

**标签映射规则（初始阈值，均为待校准参数）**：

absorption_trend_tag（v1.1 修正方向 + 冻结语义）：
```
absorption_frozen = True（带外期间）          → "冻结中"
absorption_trend_slope > +0.005（PPE 上升 = 吸收减弱）→ "锚承压中"
absorption_trend_slope < -0.005（PPE 下降 = 吸收增强）→ "锚修复中"
absorption_trend_slope = None（数据不足）     → "锚状态未知"
其他 → "锚状态稳定"
```

ols_label：
```
ols_slope = None（R² 不足）→ "无效"
ols_slope > +threshold     → "扩展"
ols_slope < -threshold     → "收缩"
|ols_slope| ≤ threshold    → "震荡"
```

cvd_label：
```
cvd_strength < 0.2         → "中性"（含 CVD 降级场景）
direction 与偏离方向一致   → "同向"
direction 与偏离方向相反   → "反向"
```

ppe_quality（v1.1 修正：基于 ppe_raw，不是 ppe_percentile）：
```
ppe_raw < 0.3  → "高阻力"
ppe_raw > 0.7  → "低阻力"
其他           → "中性"
```

**约束**：所有阈值和映射规则集中在这一个模块。实盘校准只改这里。只做原始值到标签的映射，不做新计算。

---

### 9.9 模块七：SnapshotRecorder

**职责**：组装运行时事实快照，将原始值和标签分表持久化。EventAssembler 作为本模块内部的函数层，不升级为独立顶层模块。

**输入**：全部上游模块输出（v1.1 新增 shift_magnitude 参数）

**持久化结构**：

raw_values 表（含锚基准字段，支持完整回放）：

```
bar_index, timestamp, price, open, high, low,
flip_point, band_half, anchor_freshness, anchor_shift_event, shift_magnitude,
normalized_deviation, outside_bar_count,
ppe_raw, ppe_is_spike, ppe_percentile, ppe_short_median,
ols_slope, r_squared,
cvd_direction, cvd_strength, cvd_degraded
```

labels 表（含完整重置指令记录）：

```
bar_index, timestamp,
runtime_gate, anchor_state, event_state, classification_state,
absorption_trend_tag, anchor_validity,
ols_label, cvd_label, ppe_quality,
classification_result, confidence,
reset_ols_window, freeze_absorption_trend,
reset_deviation_counter, reset_ppe_history,
reset_ppe_short_buffer
```

events 表（状态转换事件，支持行为回溯）：

```
bar_index, timestamp, event_type, event_detail
```

event_type 枚举：锚迁移 / 脱离确认 / 缺口回补确认 / GEX 过期

**事件记录规则**（v1.1 修正）：

- 锚迁移事件仅在 anchor_shift_event = True 的那一柱记录一次，不在 RESETTING 持续期间每柱重复追加。事件 detail 中包含 shift_magnitude。
- 脱离确认事件在 outside_bar_count 首次达到确认阈值时记录一次。
- 缺口回补确认事件在 gap_closure_event = True 时记录一次，detail 中包含闭合时刻的分类结论和置信度。

**约束**：只写入，不读取，不做计算。原始值和标签分表存储，互不依赖。labels 表里记录每根 Bar 的完整重置指令状态（四个布尔字段），保证行为可完整回溯。

---

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

  Step 4: ClassificationEvidence.compute(bar, instructions, cvd_degraded)
          先执行 Step3 指令（清空 / 冻结）
          再按当前状态计算因子原始值
          CVD 降级时 strength 强制归零
          更新就绪信号缓存（供下一根 Bar 的 Step3 使用）

  Step 5: LabelGenerator.generate()
          消费 Step4 原始值和 Step3 状态，生成标签

  Step 6: SnapshotRecorder.write()
          EventAssembler 组装快照，分表持久化
          事件去重：锚迁移仅在转折点记录一次

主循环不包含任何业务逻辑，只做顺序调度。
```

---

## 10. 封版补丁细节（v1.0）

以下为 v1.0 封版时的补丁记录，保留原文不做修改。

### 10.1 补丁一：raw_values 表补锚基准字段

**问题**：原始方案的 raw_values 里缺少 flip_point、band_half、anchor_freshness、shift_magnitude、outside_bar_count。这会导致事后无法回放 normalized_deviation 是基于哪一版锚和带宽算出来的，复盘时只能看到结果，无法追溯基准。

**修正**：raw_values 表补入上述五个字段。

### 10.2 补丁二：SystemStateManager 输入显式化

**问题**："runtime 计数器"是模糊表述，classification_state 的判定依赖多个独立的就绪信号，必须显式列出，否则实现阶段 classification_state 由谁决定不够明确。

**修正**：SystemStateManager 输入显式列出 ppe_history_ready、ols_window_ready、r_squared_available、deviation_confirmed 四个就绪信号，classification_state 判定规则完整定稿。

### 10.3 补丁三：冻结与重置的判定权与执行权分离

**问题**：状态机说要重置，但没有明确说"谁真正去清窗口"，容易在实现时出现状态机发出指令但无人响应的空转问题。

**修正**：SystemStateManager 只发出指令（判定权），ClassificationEvidence 和 DeviationTracker 各自执行内部缓存的清空动作（执行权）。"状态集中，缓存分治"的原则写入封版。

### 10.4 自审补充一：就绪信号循环依赖的消解

**问题**：Step 3 的 SystemStateManager 需要消费就绪信号，而就绪信号来自 Step 4 的 ClassificationEvidence，形成循环依赖。

**修正**：就绪信号使用 T-1 时刻的缓存值。ClassificationEvidence 在每根 Bar 结束时更新就绪信号缓存，SystemStateManager 在下一根 Bar 的 Step 3 读取缓存，不读取当前 Bar 的实时计算值。循环依赖彻底消除，Step 顺序不变。

### 10.5 自审补充二：labels 表记录重置指令字段

**问题**：重置指令是系统行为的重要节点。不记录的话，事后复盘时无法确认某个时刻是否触发过重置，窗口清空事件在日志里没有痕迹。

**修正**：labels 表增加四个重置指令布尔字段（reset_ols_window、freeze_absorption_trend、reset_deviation_counter、reset_ppe_history），每根 Bar 都完整记录当时的指令状态。

---

## 11. v1.1 修复日志

### 11.1 数据获取方式确认（新增第 8 节）

| 项目 | v1.0 规范描述 | v1.1 实际状态 |
|---|---|---|
| 成交数据来源 | WebSocket aggTrades 流 | Binance aggTrades REST 轮询（FMZ 沙盒约束） |
| GEX 数据来源 | GexMonitor API | GexMonitor REST API（未变） |

**影响评估**：Volume Bar 切割、PPE、OLS 在 REST 下无物理缺陷。CVD 在高流速下有 trade_id 缺口风险，通过降级机制保护（见 8.5 节）。新增第 8 节完整描述数据管道架构、REST 局限、以及 CVD 降级方案。

### 11.2 REENTRY_PENDING 状态正式纳入

| 项目 | v1.0 | v1.1 |
|---|---|---|
| 事件状态空间 | INSIDE / CANDIDATE / CONFIRMED（三态） | INSIDE / CANDIDATE / CONFIRMED / REENTRY_PENDING（四态） |
| 回带行为 | CONFIRMED → INSIDE（回带即收口） | CONFIRMED → REENTRY_PENDING → INSIDE（需 2 bar 确认） |

**修改动机**：防止单根 Bar 触碰带内边界的假闭合。脱离确认需要 2 根带外 Bar，回补确认也要 2 根带内 Bar，两个方向的确认机制对称。

**连锁影响检查**：
- SystemStateManager 的 event_state 轴：新增 REENTRY_PENDING 枚举值，与 CONFIRMED 等价处理（分类层保持输出、吸收趋势保持冻结）。
- classification_state：REENTRY_PENDING 允许到 PARTIAL / AVAILABLE（与 CONFIRMED 一致）。
- OLS 序列：REENTRY_PENDING 期间的 bar 不进入 _outside_deviations（它们的 |deviation| < 1，在带内）。
- 快照 events 表：新增 gap_closure_confirmed 事件类型。

### 11.3 OLS 窗口与分类可用性修正

**问题一：OLS 最少柱数过低**

| 项目 | v1.0 代码 | v1.1 修正 |
|---|---|---|
| ols_min_bars | 6 | 3K = 60 |

规范 7.7 节定义 OLS 窗口为 3K 根带外柱，代码将运行 OLS 的最少柱数设为 6，与规范不一致。6 根 Bar 上的线性回归缺乏统计可信度，导致系统过早输出 OLS 判断。修正为 3K = 60，与窗口大小对齐。

**问题二：classification_state 的 AVAILABLE 条件不完整**

| 检查项 | v1.0 代码 | v1.1 修正 |
|---|---|---|
| r_squared_available | 未检查 | 必须为 True |
| anchor_state = STALE | 可到 AVAILABLE | 最多到 PARTIAL |

规范 8.7 节要求 AVAILABLE 同时满足 anchor_state = FRESH 和 r_squared_available = True。v1.0 代码允许 STALE 到 AVAILABLE 且跳过 R² 检查，导致在锚数据延迟、OLS 拟合差的情况下过早宣称完整可分类。

### 11.4 OLS 序列净化

**问题**：OLS 带外偏差序列被 CANDIDATE 和 REENTRY_PENDING 阶段的 bar 污染。

| 状态 | v1.0 行为 | v1.1 修正 | 原因 |
|---|---|---|---|
| CANDIDATE | 纳入 OLS 序列 | 排除 | 未确认脱离，不应参与 |
| CONFIRMED | 纳入 OLS 序列 | 纳入（不变） | 唯一正确的数据源 |
| REENTRY_PENDING | 纳入 OLS 序列 | 排除 | 价格在带内，|deviation| < 1，混入会拉低斜率 |

### 11.5 PPE 标签映射修正

**问题一：ppe_quality 使用了错误的输入**

| 项目 | v1.0 代码 | v1.1 修正 |
|---|---|---|
| ppe_quality 输入 | ppe_percentile（相对判断） | ppe_raw（绝对判断） |

规范 8.8 节明确 ppe_quality 基于 ppe_raw 做阈值映射。两层现在各有独立阈值：锚层用 ppe_percentile（ppe_high_res_pct / ppe_low_res_pct），分类层用 ppe_raw（ppe_quality_high_resistance / ppe_quality_low_resistance）。

**问题二：吸收趋势标签方向反了**

| PPE 斜率方向 | 物理含义 | v1.0 代码标签 | v1.1 修正标签 |
|---|---|---|---|
| 正（PPE 上升） | 路径效率升高 → 吸收减弱 | 锚修复中 ✗ | 锚承压中 ✓ |
| 负（PPE 下降） | 路径效率降低 → 吸收增强 | 锚承压中 ✗ | 锚修复中 ✓ |

CONFIG 阈值同步翻转：absorption_trend_stress_slope 从 -0.005 改为 +0.005，absorption_trend_recover_slope 从 +0.005 改为 -0.005。

### 11.6 快照字段与事件记录补全

**raw_values 表新增字段**：

| 字段 | 说明 |
|---|---|
| shift_magnitude | 锚位移相对量（|位移| / band_half），v1.0 缺失 |
| ppe_is_spike | PPE 尖峰标记（True 时 ppe_raw 有值但不参与历史分布），v1.0 缺失 |
| cvd_degraded | CVD 是否处于 REST 缺口降级状态，v1.1 新增 |

**labels 表新增字段**：

| 字段 | 说明 |
|---|---|
| reset_ppe_history | 第四个重置指令布尔字段，v1.0 遗漏 |

**events 表事件去重修正**：

anchor_shift 事件从"RESETTING 期间每柱追加"改为"仅在 anchor_shift_event = True 的那一柱记录一次"。避免事件表膨胀和回溯分析误判锚偏移频率。

### 11.7 shift_magnitude 单位修正

| 项目 | v1.0 代码 | v1.1 修正 |
|---|---|---|
| detect_anchor_shift 返回值 | 绝对美元位移 | 相对量（|位移| / band_half） |

规范 8.4 节定义 shift_magnitude 为 "本次 flip_point 位移 / band_half"。v1.0 代码返回绝对美元值，导致 SystemStateManager 的 `shift_magnitude > 0.5` 判定语义错误（在 BTC $100k 时，0.5 美元几乎是零）。修正后 0.5 表示"半个带宽"，与规范一致。

### 11.8 PPE 尖峰原始值保留

| 项目 | v1.0 代码 | v1.1 修正 |
|---|---|---|
| 尖峰 Bar 的 ppe_raw | None（值丢失） | 计算并保留原始值 |
| 尖峰 Bar 进入历史分布 | 不进入（正确） | 不进入（不变） |

规范 7.3 节要求"不参与 PPE 历史分布更新，但保留原始值记录"。v1.0 的 _compute_ppe 在尖峰时直接返回 None，导致快照中 ppe_raw 也为空。v1.1 修正为返回 (ppe_raw, is_spike) 二元组：原始值写入快照，ppe_is_spike = True 时跳过历史分布插入。

### 11.9 reset_ppe_history 指令激活

**问题**：规范 8.7 节指令集明确定义 `reset_ppe_history: True 当 anchor_shift_event = True 且 shift_magnitude > 0.5`，但代码中 SystemStateManager.update() 的 anchor_shift_event 分支只设了 `reset_ols_window` 和 `reset_deviation_counter`，从未将 `reset_ppe_history` 置为 True。这意味着锚大位移后，PPE 的 20K 历史窗口不会被清空，百分位排名继续基于旧锚环境下的分布计算。

**修正**：

| 项目 | 修正前 | 修正后 |
|---|---|---|
| SSM.update() 签名 | 无 shift_magnitude 参数 | 新增 shift_magnitude 参数 |
| anchor_shift_event 分支 | 不设 reset_ppe_history | shift_magnitude > 0.5 时设为 True |
| 主循环 Step 3 调用 | 不传 shift_magnitude | 传入 shift_magnitude |

SSM.update() 新增 shift_magnitude 参数，在 `anchor_shift_event = True` 分支内检查 `shift_magnitude > anchor_shift_frac(0.5)` 时发出 `reset_ppe_history = True`。ClassificationEvidence 已有执行逻辑（清空 `_ppe_history` 和 `_ppe_short_buffer`），此前是死代码，现在被正确触发。

### 11.10 吸收趋势冻结语义完整化

**问题一：标签层缺少"冻结中"输出**

规范 7.4 节和 8.8 节要求带外冻结期间输出"冻结中"标签。代码中 `_make_absorption_trend_tag` 不检查冻结状态，带外期间仍基于冻结前的旧斜率输出"承压/稳定/修复"之一，观察者无法区分"实时判断"和"冻结快照"。

**修正**：ClassificationEvidence 的输出新增 `absorption_frozen` 布尔字段，LabelGenerator 的 `_make_absorption_trend_tag` 在 `absorption_frozen = True` 时直接返回"冻结中"，优先于任何斜率判断。

| 标签 | 触发条件 |
|---|---|
| 冻结中 | absorption_frozen = True（event_state ∉ INSIDE） |
| 锚承压中 | absorption_frozen = False 且 slope > +0.005 |
| 锚修复中 | absorption_frozen = False 且 slope < -0.005 |
| 锚状态稳定 | absorption_frozen = False 且 slope 在阈值区间内 |
| 锚状态未知 | slope = None（数据不足） |

**问题二：回带后短窗口未重置**

规范 7.4 节："价格重新回到带内时：标签解冻，从当前 Bar 开始重新积累带内序列，窗口重置。"代码在 freeze 解除后恢复接受新数据，但未清空旧数据——新 Bar 追加到冻结前的旧序列末尾，导致解冻后的首批 OLS 斜率混用了两段不同状态下的数据。

**修正**：SystemStateManager 新增 `reset_ppe_short_buffer` 指令，在 event_state 从非 INSIDE 转换为 INSIDE 时触发（包括 gap_closure 和 CANDIDATE 回带两条路径）。ClassificationEvidence 收到该指令后清空 `_ppe_short_buffer`，当前 Bar 作为新窗口的第一根数据重新积累。

### 11.11 OLS 窗口在缺口回补确认后清空

**问题**：规范 7.7 节："价格重新回到带内时，窗口清空，斜率输出空值。"代码中 `_outside_deviations` 仅在 `reset_ols_window` 指令时清空，而该指令只在 `anchor_shift_event` 时触发。`gap_closure_event` 发生时没有清空路径，导致旧的带外序列残留。后果：若价格快速再次脱离，新 CONFIRMED 柱追加到旧序列末尾，OLS 计算混用两段不同脱离事件的偏差值。

**修正**：SystemStateManager 在检测到 `gap_closure_event` 时发出 `reset_ols_window = True` 指令。ClassificationEvidence 在 Step 4 开头执行清空动作，与锚偏移清空走同一条代码路径。

| 触发场景 | reset_ols_window |
|---|---|
| anchor_shift_event = True | True（不变） |
| gap_closure_event = True | True（v1.1 新增） |

---

## 12. v1.2 架构迭代日志

v1.2 不是修复版本，而是对三个实盘运行痛点的架构级回应。每一条都伴随设计决策的第一性推演，而不是表面补丁。

### 12.1 痛点一：指标自吞噬（去趋势标准差 + 相对价格硬护栏）

**实盘现象**：价格发生极快单边运动时，慢窗标准差 `std_usd` 被方向性位移污染而暴涨。因 `band_half = std_usd × sigma_count`，吸波带瞬间膨胀。原本应被识别为"脱轴"的事件，被膨胀的防线重新罩住，`event_state` 被错误拉回 INSIDE，信号丢失。

**根因分析**：这不是阈值问题，是反馈回路的结构性缺陷。标准差度量的是"围绕均值的分散度"，但在单调趋势下这个"分散度"反映的是方向性位移而非震荡幅度。σ_slow 把位移当作波动率，band_half 就把位移当作"正常波动范围"，脱离被系统自我否认。

**方案对比**：

| 方案 | 原理 | 评估 |
|---|---|---|
| 直接 clamp = price × 1.5% | 兜底截断 | 能阻止带宽膨胀，但 std_usd 的污染值仍在系统里；下游 ppe_spike_mult × band_half 等依赖继续用污染过的基准；无法回答"波动率此刻不可信"这个语义 |
| 去趋势标准差 | 从数据源消除趋势污染 | 物理语义正确；自适应不需硬阈值；震荡行情下退化为原公式；但极端情况（数据异常、spring 异常高）仍可能失效 |
| **v1.2 采用：双层防护** | 去趋势 + 兜底护栏 | 99% 场景去趋势化已解决问题；护栏只作为 last-resort 兜底捕获剩余异常 |

**实现细节**：

```
detrended_std(values):
    对 values 做 OLS 线性拟合 y = slope × x + intercept
    residuals[i] = values[i] - (slope × i + intercept)
    return std(residuals)
```

物理意义：`σ_slow` 只度量"在当前趋势之上还剩多少震荡"。震荡行情下 slope ≈ 0，残差 ≈ 原偏差；趋势行情下拟合带走方向分量，残差保持小而稳定。

护栏阈值 `band_half_max_pct = 1.5%` 写入 CONFIG，触发时：
- `AnchorContext._band_clamped = True`
- raw_values 表 `band_clamped` 字段记录
- 状态栏 `吸收带半宽` 单元格显示 `[CLAMP]` 标注
- log 输出 WARN 级别提示

**连锁影响检查**：
- `ppe_spike_mult × band_half`：带宽回归正常后，尖峰识别不会误伤。
- `normalized_deviation = (price - flip) / band_half`：趋势中 band_half 不再膨胀，偏离值保持正确幅度，CONFIRMED 状态能正常达到。
- `anchor_shift_frac × band_half`：锚偏移阈值基于正确的 band_half，大位移事件识别更准。

### 12.2 痛点二：Chart Flag 失去准度（事件驱动重构）

**实盘现象**：图表事件 Flag 滞后，或在非关键位置集中爆发打标，严重干扰复盘视觉。

**根因分析**（经代码审计）：

1. 图表每 10 秒节流一次（`chart_update_interval_sec`），但分类结果是每根 Volume Bar 产生。多个 Bar 在同一 10 秒窗口完成时，只有最后一个 Bar 的 Flag 被打。
2. Flag 时间戳用的是 `now_ms()` 而非 Bar 完成时刻，视觉上与价格点错位。
3. 原代码基于 `classification_result not in ("部分证据", None)` 打标，只要进入 CANDIDATE 就可能触发——这是"集中爆发"的根源。
4. 不过滤置信度，LOW 和 PARTIAL 也打 Flag。

**方案对比**：

| 方案 | 评估 |
|---|---|
| 用户建议：删除所有 Flag 代码 | 简单粗暴；但图表 Flag 是系统为数不多的"实时视觉反馈"通道，删除会让观测体验退化 |
| **v1.2 采用：事件驱动重构** | 不删除，彻底重构为事件驱动 |

**v1.2 Flag 规则**：

1. **事件驱动而非状态驱动**：只在 `departure_confirmed_event`、`gap_closure_event`、`anchor_shift_event` 三类离散事件发生的那一根 Bar 上打 Flag。这些事件在 FSM 中是明确的语义转折点，天然不会集中爆发。
2. **时间戳对齐 Bar 完成时刻**：Flag 使用 `raw_row.ts_ms`（Bar 完成后 SnapshotRecorder 记录的时刻），不再用 chart 轮询时刻。
3. **置信度过滤**：
   - `departure_confirmed`：无条件打（脱离本身是结构性事件）
   - `gap_closure_confirmed`：仅 HIGH 和 MEDIUM 置信度打
   - `anchor_shift`：无条件打
4. **去重键**：维护 `_last_flagged_bar_index`，同一 Bar 的事件只打一次 Flag。
5. **Flag 文本丰富化**：标题用紧凑中文（"脱离"/"回补"/"锚移"），正文附带分类结论和置信度。

**陈年 Bug 顺手修复（v1.2）**：DeviationTracker 的 `departure_confirmed` 事件会在 `REENTRY_PENDING → CONFIRMED` 回跳时重复触发（同一轮脱离被记录两次）。v1.2 在 DeviationTracker 内部维护 `_departure_signaled` 标记，每轮脱离事件仅发一次 `departure_confirmed_event`，在 `gap_closure_event` 时重置标记供下一轮使用。

### 12.3 痛点三：高价值事件的持久化与 UI 透传

**实盘现象**：脱离、回补、锚迁移这些高价值事件只在 Log 日志中一闪而过。`_events` deque 虽然有记录，但纯内存，策略重启即灰飞烟灭。状态栏也没有事件流视图。

**方案设计**：

**子系统 A — 事件持久化**

| 设计点 | 决策 |
|---|---|
| 写入频率 | 仅在离散事件发生时追加，不是每 Bar 写整个快照。事件频率低（日均 10-50 条），I/O 可控 |
| 写入方式 | jsonl 追加写（单行 JSON），O(1) I/O，不阻塞主循环 |
| 失败处理 | 只 WARN，不异常；连续 3 次失败后自动禁用（避免日志洪泛） |
| 文件路径 | CONFIG 可配置，默认 `/tmp/alpha_radar_events.jsonl` |
| 沙盒兼容 | FMZ 无写权限时降级为纯内存模式，不影响主流程 |
| 字段结构 | `{ts_ms, bar_index, event_type, detail}`，detail 按事件类型结构化 |

**子系统 B — 状态栏事件流表**

```
┌─────────────────────────────────────────────────────────┐
│ 高价值事件流（最近 5 条）                                │
├──────────┬──────┬──────────┬─────────────────────────────┤
│ 时间     │ Bar  │ 类型     │ 细节                        │
├──────────┼──────┼──────────┼─────────────────────────────┤
│ 14:45:03 │ 1882 │ 缺口回补 │ 可恢复缺口 (HIGH)          │
│ 14:31:12 │ 1859 │ 锚迁移   │ Δ=0.87                      │
│ 14:23:51 │ 1847 │ 脱离确认 │ 上方 2.34σ                  │
│ ...                                                      │
└──────────┴──────┴──────────┴─────────────────────────────┘
```

字段设计基于交易员观测直觉：
- **时间**：UTC+8 HH:MM:SS，便于和钟表对齐
- **Bar**：事件发生的 Bar 序号，便于与图表 Flag 交叉定位
- **类型**：中文事件标签，直观可读
- **细节**：紧凑的关键参数（脱离用方向+σ值，回补用分类+置信度，锚迁移用 Δ 倍数）

最新事件在上，最多显示 N 条（`event_stream_display_size` 默认 5）。

### 12.4 v1.2 影响到的字段与配置

**raw_values 表新增字段**：

| 字段 | 含义 |
|---|---|
| band_clamped | 本柱 band_half 是否触发了 1.5% 硬护栏 |

**labels 表无新增字段**。

**events 表事件结构 detail 字段规范化**：

```python
departure_confirmed → {"normalized_deviation": float}
gap_closure_confirmed → {"classification_at_closure": str,
                          "confidence_at_closure": str}
anchor_shift → {"shift_magnitude": float}
```

**CONFIG 新增项**：

| 项 | 默认值 | 说明 |
|---|---|---|
| band_half_max_pct | 0.015 | band_half 相对价格的硬上限（1.5%） |
| event_persist_enabled | True | 是否启用事件 jsonl 持久化 |
| event_persist_path | /tmp/alpha_radar_events.jsonl | 事件文件路径 |
| event_stream_display_size | 5 | 状态栏事件流显示条数 |

**DeviationTracker 输出新增字段**：

| 字段 | 含义 |
|---|---|
| departure_confirmed_event | 本柱是否首次触发脱离确认（一次性标记） |

**DeviationTracker 内部新增状态**：

| 字段 | 含义 |
|---|---|
| `_departure_signaled` | 本轮脱离事件是否已信号过，防止 REENTRY_PENDING 回跳时重复 |

---

## 13. 已知局限声明

以下局限被明确承认，当前版本不试图解决：

**GEX 数据是理论对冲义务**：flip_point 的理论位置正确，但做市商的实际执行可能与理论义务存在偏差。GEX 更新频率是系统精度的硬上限，更新延迟期间标准化偏离的实时准确性递减。

**PPE 是 LP 行为的概率性代理**：PPE 低与 LP 积极维护的场景统计相关，但不是直接度量。两个大方向盘博弈在锚附近可以产生与 LP 积极吸收相同的 PPE 读数，在没有订单簿数据的前提下无法完全区分。

**band_half 参数是经验值**：tanh 分母"5"无物理推导来源，σ_slow 计算窗口待校准。两者标注为"待实盘校准"，不是物理推导结论。

**OLS 在非线性过程下失效**：R² 过滤是部分保护，不是完全保护。先扩展后收缩的偏离路径可能产生接近零的斜率，与震荡读数混淆。

**CVD 强度门阈值是经验值**：0.2 的初始设定需要实盘数据验证，不同流速环境下可能需要动态调整。

**REST 轮询下 CVD 有数据完整性风险**（v1.1 新增）：Binance aggTrades REST 单次上限 1000 条，高流速下可能丢失成交。降级机制提供保护性截断（strength 归零），但无法回补丢失的数据。当运行环境支持 WebSocket 时，应优先切换。

**Volume Bar 成交量单位 N 待实测确定**：与 REST 轮询延迟和市场流速相关，需要在真实连接环境下实测后校准，不在概念层拍定。

**系统不解释所有行情**：classification_state = UNAVAILABLE 或 PARTIAL 时，系统主动标注不确定性，不强行输出分类结论。这是设计特性，不是缺陷。

---

*文档完。v1.1。*
