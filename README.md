# Alpha Radar — 项目设计规范文档

> 版本：封版 v1.0  
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
8. [模块化设计](#8-模块化设计)
9. [封版补丁细节](#9-封版补丁细节)
10. [已知局限声明](#10-已知局限声明)

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

围绕当前有效定价锚的价格容忍区间。在该区间内，价格波动优先被解释为均衡内扰动，而非有效脱离。宽度由 band_half 定义，非对称情形暂不引入，第一版使用对称带宽。

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
  ↓
定价中心迁移  /  重新吸收（可回补缺口）
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

**两阶段设计**：
- 候选脱离：标准化偏离 ≥ 1，但带外持续 Bar 计数 < 2。
- 确认脱离：带外持续 Bar 计数 ≥ 2，进入分类判定层。

**输出**：脱离状态（带内 / 候选 / 确认）+ 当前标准化偏离值 + 带外持续 Bar 计数

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
| flip_point | 锚判定层 | 锚位置定义 | GexMonitor API |
| band_half | 锚判定层 | 吸收带边界 | GexMonitor API |
| PPE 百分位 | 锚判定层 | 当前吸收强度（瞬时） | WebSocket aggTrades |
| 带内吸收趋势标签 | 锚判定层 | 吸收强度趋势（历史） | WebSocket aggTrades |
| 标准化偏离 | 脱离判定层 | 带内外状态 + 偏离幅度 | 实时价格 + 锚层 |
| 带外持续 Bar 计数 | 脱离判定层 | 真实脱离确认 | Volume Bar 序列 |
| OLS 斜率（R² 强绑定） | 分类判定层 | 偏离扩展速率（时间维度） | 带外 Bar 序列 |
| CVD 方向（强度门） | 分类判定层 | 成交方向性压力（成交维度） | WebSocket aggTrades |
| PPE 原始值 | 分类判定层 | 路径质量辅助证据（空间维度） | Volume Bar |

**附门控**：anchor_freshness（不计入因子数，前置有效性开关）

**窗口参数**：全部由单一基础参数 K 派生，K 是系统唯一需要实盘校准的核心参数。

```
K = 基础窗口单位（初始建议值：20 根 Volume Bar）

CVD 窗口          = K
PPE 短窗口        = K
OLS 窗口          = 3K（仅带外 Bar 参与计算）
PPE 历史窗口      = 20K
带内吸收趋势窗口  = K（带内 Bar 短窗中位数）
```

---

### 7.1 flip_point

**物理含义**：做市商 delta 对冲方向发生反转的价格水平。在此价格以上，做市商对冲行为倾向于卖出现货；在此价格以下，倾向于买入现货。这个对冲义务的方向切换点天然构成对价格的双向约束，是定价锚的位置定义。

**来源**：GexMonitor API 直接取值，不做任何加工。

**更新触发**：GEX 快照更新时同步更新。flip_point 位移超过 `0.5 × band_half` 时，触发 anchor_shift_event，下游模块响应重置。

**已知局限**：GEX 数据描述的是理论对冲义务，不是实际执行行为。flip_point 的理论位置正确，但做市商的实际对冲行为可能与理论义务存在偏差。

---

### 7.2 band_half

**物理含义**：吸收带的半宽度。带宽由两个量共同决定：σ_slow 反映当前局部波动率水平，spring 容量反映做市商在单位波动上能承受的对冲压力。带宽越宽说明做市商弹性越强，带宽越窄说明锚越脆弱。

**计算公式**（继承自 Gamma Spatial Observer v6）：

```
sigma_count = 3 + 3 × tanh(spring_capacity_per_sigma / 5)
band_half   = σ_slow × sigma_count
```

sigma_count 范围自适应在 [3, 6] 之间。

**参数标注**：tanh 分母"5"是经验设定值，无物理推导来源。σ_slow 的计算窗口是待校准参数。两者在文档里明确标注为"待实盘校准"，不是物理推导结论。

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

**针刺保护**：当 `high - low > 1.5 × band_half` 时，该 Bar 的 PPE 标记为异常，不参与 PPE 历史分布更新，但保留原始值记录。

**在锚判定层的使用**：

```
PPE_percentile = 当前 PPE 在过去 20K 根 Bar 历史分布中的百分位

PPE_percentile < 0.3：吸收强，锚效用获得流动性支撑
PPE_percentile > 0.7：吸收弱，锚效用缺乏流动性支撑
0.3 ≤ PPE_percentile ≤ 0.7：中性，不单独表态
```

**在分类判定层的使用**：使用 PPE 原始值作为空间维度的吸收状态辅助证据，定位为辅助项，不作硬门槛。

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
锚承压中：PPE 中位数趋势性升高，吸收强度递减
锚状态稳定：PPE 中位数无明显趋势
锚修复中：PPE 中位数趋势性降低，吸收强度增强
```

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

**计算窗口限定**：

- 仅使用带外 Bar 参与计算（不混入带内 Bar）。
- 从价格首次确认脱离（带外持续 Bar 计数 ≥ 2）的 Bar 开始计时。
- 价格重新回到带内时，窗口清空，斜率输出空值。
- anchor_shift_event 触发时，窗口强制重置。

**已知边界条件**：当偏离序列先扩展后收缩（或反之）时，OLS 斜率会输出接近零的值，与"偏离震荡"的读数相同但含义不同。R² 过滤是部分保护，不是完全保护。非线性过程在 R² 过滤后标注为"非线性过程"，不强行输出分类结论。

---

### 7.8 CVD 方向（强度门）

**物理含义**：在当前窗口内，成交力量是否在持续朝某一方向积累，以及这种积累的强度。CVD 方向强且与价格偏离方向一致，说明脱离被真实成交支撑。

**计算方式**：

```
cvd_delta（每根 Bar）= 买方 Taker 成交量 - 卖方 Taker 成交量

CVD_direction = sign(Σ cvd_delta，过去 K 根 Bar)
CVD_strength  = |Σ cvd_delta| / (K × average_volume_per_bar)
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

**数据来源**：WebSocket aggTrades 流，实时累计，不使用 REST 轮询构造（REST 在活跃行情下存在成交缺口风险）。

---

## 8. 模块化设计

### 8.1 设计原则

模块化的目标是解决复杂系统在实盘调试时的定位困难问题。设计遵循以下原则：

- **状态集中，缓存分治**：所有状态切换规则集中在 SystemStateManager，各模块维护各自内部缓存，接收状态机指令后执行对应的清空或冻结动作。
- **原始值与标签分离**：所有因子先输出原始值，LabelGenerator 统一做原始值到标签的映射，阈值集中定义，校准时只改一处。
- **数据管道独立可监控**：WebSocket 数据质量问题（断线、缺口）与因子计算问题必须能独立定位，不互相污染。
- **主循环只做调度**：主循环不包含任何业务逻辑，只做顺序调度。

### 8.2 7 模块结构总览

```
BarAssembler           数据管道层
AnchorContext          锚计算层
DeviationTracker       脱离计算层
ClassificationEvidence 分类证据层
SystemStateManager     状态机层
LabelGenerator         标签生成层
SnapshotRecorder       快照存储层
```

---

### 8.3 模块一：BarAssembler

**职责**：把 WebSocket aggTrades 流实时聚合成 Volume Bar。

**输入**：逐笔成交（价格、成交量、aggressor side、时间戳）

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
  completed_at:  int      # 完成时间戳（毫秒）
}
```

**约束**：只做聚合，不做任何计算或判断。Volume Bar 成交量阈值 N 是这里唯一的配置参数，标注为待实测校准。

**独立的理由**：WebSocket 断线重连、Bar 边界对齐、数据缺口等问题必须能单独测试和监控。把数据管道埋在其他模块里，数据质量问题会被误认为因子计算问题。

---

### 8.4 模块二：AnchorContext

**职责**：从 GEX 快照计算锚的位置和宽度，输出门控状态。

**输入**：GEX 快照（flip_point、spring 容量、σ_slow）、上次 GEX 更新时间戳

**输出**：

```python
{
  flip_point:           float,  # 锚位置
  band_half:            float,  # 吸收带半宽度
  anchor_freshness:     str,    # FRESH / STALE / EXPIRED
  anchor_shift_event:   bool,   # 本次更新是否触发锚迁移
  shift_magnitude:      float   # 本次 flip_point 位移 / band_half
}
```

**anchor_freshness 判定规则**：

```
FRESH：GEX 数据更新间隔在正常范围内
STALE：更新间隔超过阈值但未达到 EXPIRED
EXPIRED：更新间隔过长，坐标系不可信，下游全部降级
```

具体时间阈值根据 GexMonitor API 实际更新频率确定。

**约束**：只处理 GEX 层，不碰成交数据，不输出任何效用判断或标签。锚迁移事件在这里检测，但响应逻辑（触发下游重置）在 SystemStateManager 里处理。

---

### 8.5 模块三：DeviationTracker

**职责**：计算标准化偏离和脱离确认状态。

**输入**：当前 Volume Bar（close 价格）、flip_point、band_half

**输出**：

```python
{
  normalized_deviation:  float,  # (price - flip_point) / band_half
  outside_bar_count:     int,    # 当前带外持续 Bar 数量
  deviation_confirmed:   bool    # 带外持续 Bar 计数 ≥ 2
}
```

**内部规则**：

- 每根带外 Bar，outside_bar_count += 1。
- 价格回到带内时，outside_bar_count 清零，deviation_confirmed = False。
- 收到 SystemStateManager 的 reset_deviation_counter 指令时，outside_bar_count 清零。

**约束**：只回答有没有脱离、是否连续确认，不碰 CVD、OLS、PPE 的任何值。

---

### 8.6 模块四：ClassificationEvidence

**职责**：计算全部因子原始值，输出分类层所需的证据数据。

**输入**：Volume Bar 序列、当前空间状态（来自 SystemStateManager）、SystemStateManager 发出的重置/冻结指令

**输出**：

```python
{
  # 锚层 PPE
  ppe_raw:              float,        # 当前 Bar PPE 原始值
  ppe_percentile:       float,        # 历史 20K 窗口百分位
  ppe_short_median:     float,        # 带内 K 窗口中位数（趋势标签上游）

  # 分类层证据
  ols_slope:            float | None, # OLS 斜率（R² 不足时为 None）
  r_squared:            float,        # 与 ols_slope 强绑定
  cvd_direction:        int,          # 原始方向符号（+1 / -1 / 0）
  cvd_strength:         float,        # 归一化强度 [0, 1]

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

reset_ppe_history = True：
  → 清空 PPE 历史窗口
  → ppe_history_ready = False
```

**OLS 计算限制**：

- 仅带外 Bar 参与。
- 带内 Bar 进入 ppe_short_median 序列，带外时冻结该序列。
- ols_slope 和 r_squared 必须作为绑定对输出，不可分离。

**约束**：只产出原始证据值，不产出任何标签，不产出"缺口 / 迁移"结论。

---

### 8.7 模块五：SystemStateManager

**职责**：维护四轴状态机，决定当前哪些因子有效、哪些冻结、哪些降级，并发出重置 / 冻结指令。

**输入**：

```
来自 AnchorContext：    anchor_freshness, anchor_shift_event, shift_magnitude
来自 DeviationTracker：normalized_deviation, outside_bar_count, deviation_confirmed
就绪信号（T-1 缓存）：  ppe_history_ready, ols_window_ready,
                        r_squared_available, deviation_confirmed
```

**就绪信号使用 T-1 缓存值**：就绪信号由 ClassificationEvidence 在每根 Bar 结束后更新并缓存，SystemStateManager 在下一根 Bar 的 Step 3 里读取缓存值，避免 Step 3 和 Step 4 的循环依赖。

**四轴状态定义**：

```python
runtime_gate:         "COLD_START" | "READY"
anchor_state:         "FRESH" | "STALE" | "EXPIRED" | "RESETTING"
event_state:          "INSIDE" | "CANDIDATE" | "CONFIRMED"
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
INSIDE：|normalized_deviation| < 1
CANDIDATE：|normalized_deviation| ≥ 1 且 outside_bar_count < 2
CONFIRMED：outside_bar_count ≥ 2
CONFIRMED → INSIDE：价格重新回到带内
```

classification_state：
```
UNAVAILABLE：
  runtime_gate = COLD_START
  或 anchor_state IN [EXPIRED, RESETTING]
  或 event_state ≠ CONFIRMED

PARTIAL：
  event_state = CONFIRMED
  且 anchor_state IN [FRESH, STALE]
  且 ppe_history_ready = True
  且 (ols_window_ready = False 或 r_squared_available = False)

AVAILABLE：
  event_state = CONFIRMED
  且 anchor_state = FRESH
  且 ppe_history_ready = True
  且 ols_window_ready = True
  且 r_squared_available = True
```

**发出的指令集**：

```python
{
  reset_ols_window:        bool,  # True 当 anchor_shift_event = True
  freeze_absorption_trend: bool,  # True 当 event_state ≠ INSIDE
  reset_deviation_counter: bool,  # True 当 anchor_state = RESETTING
  reset_ppe_history:       bool   # True 当 anchor_shift_event = True
                                  #       且 shift_magnitude > 0.5
}
```

**约束**：SystemStateManager 是唯一可以修改四轴状态的模块。其他模块只读取状态，不写入状态。状态机只发出指令，不直接持有各模块的内部缓存窗口。

---

### 8.8 模块六：LabelGenerator

**职责**：消费 ClassificationEvidence 的原始值和 SystemStateManager 的四轴状态，生成所有标签。

**输入**：ClassificationEvidence 全部原始值、SystemStateManager 四轴状态 + 指令

**输出**：

```python
{
  # 锚层标签
  absorption_trend_tag: str,  # 锚承压中 / 锚状态稳定 / 锚修复中 / 冻结中
  anchor_validity:      str,  # 有效 / 承压 / 过期 / 重置中

  # 分类层标签
  ols_label:            str,  # 扩展 / 收缩 / 震荡 / 无效
  cvd_label:            str,  # 同向 / 反向 / 中性
  ppe_quality:          str   # 高阻力 / 中性 / 低阻力
}
```

**标签映射规则（初始阈值，均为待校准参数）**：

absorption_trend_tag：
```
freeze_absorption_trend = True → "冻结中"
ppe_short_median 趋势显著上升  → "锚承压中"
ppe_short_median 趋势显著下降  → "锚修复中"
其他                           → "锚状态稳定"
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
cvd_strength < 0.2         → "中性"
direction 与偏离方向一致   → "同向"
direction 与偏离方向相反   → "反向"
```

ppe_quality：
```
ppe_raw < 0.3  → "高阻力"
ppe_raw > 0.7  → "低阻力"
其他           → "中性"
```

**约束**：所有阈值和映射规则集中在这一个模块。实盘校准只改这里。只做原始值到标签的映射，不做新计算。

---

### 8.9 模块七：SnapshotRecorder

**职责**：组装运行时事实快照，将原始值和标签分表持久化。EventAssembler 作为本模块内部的函数层，不升级为独立顶层模块。

**输入**：全部上游模块输出

**持久化结构**：

raw_values 表（含锚基准字段，支持完整回放）：

```
bar_index, timestamp, price,
flip_point, band_half, anchor_freshness, shift_magnitude,
normalized_deviation, outside_bar_count,
ppe_raw, ppe_percentile, ppe_short_median,
ols_slope, r_squared,
cvd_direction, cvd_strength
```

labels 表（含重置指令记录）：

```
bar_index,
runtime_gate, anchor_state, event_state, classification_state,
absorption_trend_tag, anchor_validity,
ols_label, cvd_label, ppe_quality,
reset_ols_window, freeze_absorption_trend,
reset_deviation_counter, reset_ppe_history
```

events 表（状态转换事件，支持行为回溯）：

```
bar_index, event_type, event_detail
```

event_type 枚举：锚迁移 / 脱离确认 / 回归带内 / OLS 窗口重置 / 冷启动结束 / GEX 过期

**约束**：只写入，不读取，不做计算。原始值和标签分表存储，互不依赖。labels 表里记录每根 Bar 的完整重置指令状态，保证行为可完整回溯。

---

### 8.10 主循环结构

```
每根 Volume Bar 完成时（BarAssembler 触发）：

  Step 1: AnchorContext.check_update()
          如有新 GEX 快照则更新，检测 anchor_shift_event

  Step 2: DeviationTracker.update(bar)
          更新 normalized_deviation 和带外持续计数

  Step 3: SystemStateManager.update()
          读取 Step1、Step2 输出 + T-1 就绪信号缓存
          推进四轴状态，发出重置 / 冻结指令

  Step 4: ClassificationEvidence.compute(bar, instructions)
          先执行 Step3 指令（清空 / 冻结）
          再按当前状态计算因子原始值
          更新就绪信号缓存（供下一根 Bar 的 Step3 使用）

  Step 5: LabelGenerator.generate()
          消费 Step4 原始值和 Step3 状态，生成标签

  Step 6: SnapshotRecorder.write()
          EventAssembler 组装快照，分表持久化

主循环不包含任何业务逻辑，只做顺序调度。
```

---

## 9. 封版补丁细节

### 9.1 补丁一：raw_values 表补锚基准字段

**问题**：原始方案的 raw_values 里缺少 flip_point、band_half、anchor_freshness、shift_magnitude、outside_bar_count。这会导致事后无法回放 normalized_deviation 是基于哪一版锚和带宽算出来的，复盘时只能看到结果，无法追溯基准。

**修正**：raw_values 表补入上述五个字段，封版字段列表见 8.9 节。

### 9.2 补丁二：SystemStateManager 输入显式化

**问题**："runtime 计数器"是模糊表述，classification_state 的判定依赖多个独立的就绪信号，必须显式列出，否则实现阶段 classification_state 由谁决定不够明确。

**修正**：SystemStateManager 输入显式列出 ppe_history_ready、ols_window_ready、r_squared_available、deviation_confirmed 四个就绪信号，classification_state 判定规则完整定稿，见 8.7 节。

### 9.3 补丁三：冻结与重置的判定权与执行权分离

**问题**：状态机说要重置，但没有明确说"谁真正去清窗口"，容易在实现时出现状态机发出指令但无人响应的空转问题。

**修正**：SystemStateManager 只发出指令（判定权），ClassificationEvidence 和 DeviationTracker 各自执行内部缓存的清空动作（执行权）。"状态集中，缓存分治"的原则写入封版。

### 9.4 自审补充一：就绪信号循环依赖的消解

**问题**：Step 3 的 SystemStateManager 需要消费就绪信号，而就绪信号来自 Step 4 的 ClassificationEvidence，形成循环依赖。

**修正**：就绪信号使用 T-1 时刻的缓存值。ClassificationEvidence 在每根 Bar 结束时更新就绪信号缓存，SystemStateManager 在下一根 Bar 的 Step 3 读取缓存，不读取当前 Bar 的实时计算值。循环依赖彻底消除，Step 顺序不变。

### 9.5 自审补充二：labels 表记录重置指令字段

**问题**：重置指令是系统行为的重要节点。不记录的话，事后复盘时无法确认某个时刻是否触发过重置，窗口清空事件在日志里没有痕迹。

**修正**：labels 表增加四个重置指令布尔字段（reset_ols_window、freeze_absorption_trend、reset_deviation_counter、reset_ppe_history），每根 Bar 都完整记录当时的指令状态。

---

## 10. 已知局限声明

以下局限被明确承认，第一版不试图解决：

**GEX 数据是理论对冲义务**：flip_point 的理论位置正确，但做市商的实际执行可能与理论义务存在偏差。GEX 更新频率是系统精度的硬上限，更新延迟期间标准化偏离的实时准确性递减。

**PPE 是 LP 行为的概率性代理**：PPE 低与 LP 积极维护的场景统计相关，但不是直接度量。两个大方向盘博弈在锚附近可以产生与 LP 积极吸收相同的 PPE 读数，在没有订单簿数据的前提下无法完全区分。

**band_half 参数是经验值**：tanh 分母"5"无物理推导来源，σ_slow 计算窗口待校准。两者标注为"待实盘校准"，不是物理推导结论。

**OLS 在非线性过程下失效**：R² 过滤是部分保护，不是完全保护。先扩展后收缩的偏离路径可能产生接近零的斜率，与震荡读数混淆。

**CVD 强度门阈值是经验值**：0.2 的初始设定需要实盘数据验证，不同流速环境下可能需要动态调整。

**Volume Bar 成交量单位 N 待实测确定**：与 WebSocket 实际消息延迟相关，需要在真实连接环境下实测后校准，不在概念层拍定。

**系统不解释所有行情**：classification_state = UNAVAILABLE 或 PARTIAL 时，系统主动标注不确定性，不强行输出分类结论。这是设计特性，不是缺陷。

---

*文档完。封版 v1.0。*
