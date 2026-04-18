# Alpha Radar — 项目设计规范文档

> 版本：v1.3.4（锚健康度可用性语义收口 + 减冗清理）
> 前版：v1.3.3 → v1.3.2 → v1.3.1 → v1.3 → v1.2 → v1.1 → 封版 v1.0
> 性质：研究型观测系统，非交易执行系统

---

## v1.3.4 版本变更摘要

### 本轮主题（一句话）

修复锚健康度长期显示"未就绪"的 readiness 层级绑定错误——把 `anchor_health_score` 的 readiness 语义从"全局系统就绪"（错误绑定）降级为"锚层自身就绪"（正确），并顺带清理上一轮减冗审计识别出的 9 项冗余。

### 问题现象（实盘观察）

**输入**：系统已连续运行 3 小时、成交量柱 ~300 根、GEX freshness = FRESH、aggTrades + GEX 采集正常、综述日志有 PPE% 输出、状态栏《带内结构健康评估》的 ED1 / sign_consistency / center_loss / 侵蚀方向 / 冻结状态 / 窗口进度均有值。

**异常**：锚健康度在这 3 小时内从未出现，始终显示"未就绪"。

### 根因判断

v1.3 首版在 `_compute_anchor_health()` 顶部加了一条一票否决：

```python
if runtime_gate == "COLD_START":
    return None, "未就绪", None
```

而 `runtime_gate` 转 READY 的条件是 `ppe_history_ready`——即 `len(_ppe_history) >= 20K = 400 根 Bar`。这是**分类层**就绪条件（分类矩阵需要 PPE 百分位作为阻力辅助证据），**不是锚层**就绪条件。3 小时 300 根 Bar 的实盘场景下，系统距离 400 根阈值还差 100 根，于是 `runtime_gate` 永远停在 `COLD_START`，health 被一票否决。

**本质是 v1.3 首版的层级混淆**：把"整体系统未完全就绪"和"锚健康度不可计算"错误地合并到了同一条件上。

### H-1 修复（根因层修复）

`_compute_anchor_health()` 不再感知 `runtime_gate`。health = None 的合法触发收敛为两种：

```python
if not anchor_source_ts_ms:     # GEX 从未成功获取过
    return None, "未就绪", None
if anchor_state == "EXPIRED":   # 锚参照系已失效（v1.3.1 Fix-2 保留）
    return None, "未就绪", None
```

其他场景 health 可计算；各因子缺值时沿用规范 §13.3 已定的中性 0.5 策略。

### H-2 顺带清理（减冗审计积压）

| 项 | 类型 |
|---|---|
| 删 CONFIG 键 `absorption_trend_window` / `ppe_high_res_pct` / `ppe_low_res_pct` | 配置死键（无消费方） |
| 删 `raw_evidence` return dict 中的 `cvd_sum` | 生产无消费字段（局部变量仍保留用于 direction 判定） |
| 删 `BarAssembler.get_current_price()` | 零调用死 getter |
| 删 `AnchorContext.get_last_fetch_attempt_ms()` | 零调用死 getter |
| 清理 ~15 处 `Q1/Q2/Q3/Q4/Q5 修正` / `额外提醒一二修正` / `痛点 1/3` 老编号 | 历史标记（保留内容，只删纯编号） |
| 删 `_render_status` docstring 的 `(原 _update_status)` | 过期引用 |

### 不改变的范围

- **四轴状态机定义**：`runtime_gate / anchor_state / event_state / classification_state` 完全不动。
- **分类矩阵、Anchor 判定、中心性因子计算**：不动。
- **健康度仍是纯观测**：不进入任何状态机门控，不触发事件，不新增图表元素。
- **v1.3.2 time-driven / event-driven 调度边界**：完全保留。
- **v1.3.2 catch-up drain、Display 三态语义、LogProfit 健康度出口**：完全保留。

**本轮没有新增任何模块、状态轴、事件类型、状态栏表、CONFIG 语义字段**。健康度 readiness 字段也没新增（我审视过是否需要 `health_unready_reason`，结论是不需要——见 §18.4）。

---

## 目录

1. 项目背景 / 2. 第一性目的 / 3. 研究边界 / 4. 核心术语 / 5. 研究主链
6. 三个判定层 / 7. 因子集 / 8. 数据获取方式 / 9. 模块化设计
10. 封版补丁 v1.0 / 11. v1.1 修复日志 / 12. v1.2 架构迭代 / 13. v1.3 架构迭代
14. 已知局限声明 / 15. v1.3.1 修补日志 / 16. v1.3.2 迭代日志 / 17. v1.3.3 迭代日志
**18. v1.3.4 迭代日志（本版）**

---

## 1-13 节

章节内容在 v1.0 至 v1.3.3 规范中完整给出，v1.3.4 未变更。

**唯一需要订正的局部**是 §13.3 的"缺值策略"描述——见 §18.2。

---

## 14. 已知局限声明

[v1.0 - v1.3.3 的已知局限全部保留。]

v1.3.4 **未新增已知局限**——本轮是"修正既有实现与既有意图的偏离"和"清理冗余"，不改变任何根本约束。

---

## 15. v1.3.1 修补日志

[v1.3.1 内容不变（Fix-1 到 Fix-6）。]

---

## 16. v1.3.2 迭代日志

[v1.3.2 内容不变（E-1 到 E-5）。]

---

## 17. v1.3.3 迭代日志

[v1.3.3 内容不变（T-1 到 T-6）。]

---

## 18. v1.3.4 迭代日志

### 18.1 根因审计（Phase 1 复述）

逐级审计结果：

| 审计路径 | 结论 |
|---|---|
| **Path A**：raw_evidence 层字段 | ✅ 无问题。`erosion_drift / ed1_raw / sign_consistency / center_loss / ppe_percentile` 全部正常产出，状态栏能显示所有中心性分解值。 |
| **Path B**：LabelGenerator._compute_anchor_health | ❌ **根因所在**。顶部 `if runtime_gate == "COLD_START": return None, ...` 一票否决。 |
| **Path C**：Display._build_health_table | ✅ 无问题。忠实反映上游 None，展示为"未就绪"。 |
| **Path D**：Snapshot 与日志 | ✅ 无问题。快照里 `anchor_health_score` 字段一直是 None（忠实记录），说明上游没算。 |

### 18.2 读者容易忽略的语义纠偏（规范 §13.3 订正）

v1.3 规范 §13.3 在"缺值策略"清单中写了：

> runtime_gate == COLD_START → score = None, level = "未就绪"

这条**不是第一性的物理纪律**，而是 v1.3 首版设计时的一次层级混淆。正确的缺值策略应当只包含两种 readiness 早退，以及四个因子的中性回退。下面是 v1.3.4 订正后的完整缺值策略：

| 输入 | 处理 |
|---|---|
| `anchor_source_ts_ms` 缺失或为 0（GEX 从未成功） | score = None, level = "未就绪" |
| `anchor_state == "EXPIRED"`（锚参照系失效） | score = None, level = "未就绪" |
| `erosion_drift = None`（中心性冷启动或参照失） | `H_erosion = 0.5`（中性） |
| `center_loss = None` | `H_center_loss = 0.5` |
| `ppe_percentile = None`（PPE 历史尚浅） | `H_micro = 0.5` |
| 非 RESETTING 状态（FRESH / STALE） | `H_stability = 1.0` |

**`runtime_gate` 不在缺值策略里**。这是规范层面的语义订正。

### 18.3 "锚层就绪"与"分类层就绪"的语义分离

v1.3.4 把这两个 readiness 语义显式分开：

| 属性 | 锚层就绪（health 用） | 分类层就绪（classification_state 用） |
|---|---|---|
| 物理动机 | 观察"锚的中心性与时效"本身 | 输出"脱离分类结论"需要充足历史分布 |
| 消费方 | `anchor_health_score` 观测 | `classification_state ∈ {UNAVAILABLE, PARTIAL, AVAILABLE}` |
| 就绪条件 | GEX 成功过一次 AND anchor_state != EXPIRED | `runtime_gate = READY` AND `ols_window_ready` AND `r_squared_available` |
| 典型触发时间 | 系统启动 60 秒内（GEX 首次 fetch） | 系统启动 5~6 小时后（20K 柱 PPE 满窗 + 3K 柱 OLS 满窗） |
| 在 v1.3 ~ v1.3.3 的实现 | ❌ 错误地继承了分类层的条件 | ✅ 正确 |
| 在 v1.3.4 的实现 | ✅ 独立最小就绪 | ✅ 不变 |

**这是 v1.3.4 的核心规范性贡献**——把两个 readiness 彻底解耦，消除层级混淆的复发可能。

### 18.4 为什么不引入 `health_unready_reason` 字段

审视过是否需要在 labels 表新增 `health_unready_reason ∈ {"GEX 从未获取", "锚已过期"}` 供回放。结论：**不需要**。

理由：
1. health = None 的合法触发在 v1.3.4 已收窄为两种，且这两种都能从现有字段外部识别——"GEX 从未获取"等价于 `raw_values.flip_point is None` 或 `labels.anchor_validity == "未知"`；"锚已过期"等价于 `labels.anchor_validity == "已过期"`。新增字段是冗余。
2. 修复后 health = None 在正常运行期只会出现在极端边缘场景：系统启动第 1~60 秒（GEX 60s 节流下首次 fetch 之前）或连续 60 分钟 GEX 失败。观察者看到"未就绪"已经能直接理解上下文。
3. 反过拟合纪律：**先证明必要再加字段**。本轮没有证据证明必要。

### 18.5 `_compute_anchor_health` 方法签名

修复后本方法仍接收 `system_state` 参数——原因是方法仍然需要：
- `anchor_state` 用于 EXPIRED 早退判断
- `anchor_state == "RESETTING"` 时 H_stability 做线性爬升

**本方法不再读取 `system_state["runtime_gate"]`**。v1.3.4 只是让这个参数的消费面减小了一项。签名未变，向后兼容。

### 18.6 H-2 减冗清理明细

本轮顺带清掉上一轮减冗审计识别的 9 项冗余（审计报告 R-1 ~ R-9）：

| 编号 | 位置 | 动作 | 净行数变化 |
|---|---|---|---|
| R-1 | CONFIG `absorption_trend_window` | 删除（无消费方） | -1 |
| R-2 | CONFIG `ppe_high_res_pct` | 删除（无消费方） | -1 |
| R-3 | CONFIG `ppe_low_res_pct` | 删除（无消费方） | -1 |
| R-4 | `raw_evidence["cvd_sum"]` | 从 return dict 删除（内部局部变量仍保留） | -1 |
| R-5 | `BarAssembler.get_current_price()` | 删除（零调用） | -3 |
| R-6 | `AnchorContext.get_last_fetch_attempt_ms()` | 删除（零调用） | -3 |
| R-7 | ~15 处 Q1/Q2/.../额外提醒/痛点 老编号 | 替换（保留内容，只删纯编号前缀） | 0（同行替换） |
| R-8 | `v1.2 修正 (痛点 1):` / `v1.2 增强 (痛点 3):` | 同上 | 0 |
| R-9 | `_render_status` docstring 里 `(原 _update_status)` | 删除 | 0 |

**净减行数约 10**。保留的是所有具有实质信息的解释与 `(规范 X.Y 节)` 引用——这些编号前缀（Q1/痛点 X）删掉只损失"这是第几次修补"的无信息元数据，不损失技术内容。

### 18.7 什么在 v1.3.4 仍然应该显示"未就绪"

**合法的未就绪场景**：

1. **系统刚启动 1~60 秒**：`anchor_source_ts_ms` 仍为 0，GEX 还没 fetch 到过一次。health = None / "未就绪" 是**正确**的——此时连锚位置都还没有。
2. **GEX 已 EXPIRED**：`anchor_state == "EXPIRED"`，即 GEX 距离上次成功超过 60 分钟。health = None / "未就绪" 是**正确**的——此时中心性因子都是基于过期锚位置的，继续给分是伪精度。

**不应该显示"未就绪"的场景**（v1.3.4 修复对象）：

1. ~~`runtime_gate == COLD_START` 但 GEX FRESH~~：现在能算。
2. ~~`ppe_history` 未满 400 根~~：现在不影响 health（只影响 classification_state）。
3. ~~中心性窗口在冷启动 `centrality_buffer_len < K`~~：`erosion_drift` 会为 None，但走 0.5 中性，score 仍能算。

### 18.8 反过拟合审查（本轮）

| 风险 | 规避方式 |
|---|---|
| 把 UI 修好了但底层逻辑没修 | ❌ 本轮改的是 `_compute_anchor_health`（LabelGenerator 层），不是 `_build_health_table`（Display 层） |
| 新增状态轴或 readiness 字段 | ❌ 没有。本轮是"删一行早退 + 让既有两行早退承担全部责任" |
| 把 health 的 None 语义扩大 | ❌ 本轮反向——收窄 None 合法触发 |
| 为了修 bug 引入新 CONFIG | ❌ 没有新 CONFIG |
| 破坏 v1.3.2 time-driven 调度 | ❌ 本轮只动 LabelGenerator 内部逻辑 + CONFIG 清理 + getter 删除，调度层未动 |
| 破坏 v1.3.3 《数据采集》表 | ❌ 该表由实时 getter 驱动，本轮未改它的数据源 |
| 绕路搞显示修补 | ❌ 本轮根本没碰 Display 层（除了清理 docstring 里一处历史引用） |

---

## 19. 验收说明

### 19.1 为什么修复后锚健康度应能出现

**场景完全对应你描述的实盘状态**：

| 观察项 | 实盘现象 | 修复后期望行为 |
|---|---|---|
| 连续运行 3 小时 | ✓ | anchor_source_ts_ms 早就非 0（第 60 秒内 GEX 首次 fetch 成功） |
| 柱数 ~300（<400） | ✓ | `runtime_gate = COLD_START`——**但本轮已不再阻断 health** |
| GEX FRESH | ✓ | `anchor_state = FRESH`——不走 EXPIRED 早退 |
| 中心性 ED1 / sign / 离心有值 | ✓ | `erosion_drift`、`center_loss` 非 None，H_erosion / H_center_loss 走正常 sigmoid |
| PPE% 有值 | ✓ | `ppe_percentile` 非 None，H_micro 走正常 sigmoid |
| 冻结=是（CONFIRMED 期） | ✓ | `_last_centrality_values` 透出冻结前最后有效值，health 仍能算 |

**修复后首次出现 health 的时点**：GEX 首次 fetch 成功之后的**下一次 tick_status 刷新**（即 5 秒内）。实际会在系统启动 60 秒内显示第一个 health 分数。

### 19.2 仍应显示"未就绪"的场景

- 系统启动后 1~60 秒（GEX 尚未 fetch 成功）
- 连续 60+ 分钟 GEX fetch 失败（anchor_state = EXPIRED）
- 极端异常：GEX 服务完全下线

这三种是**真正的"系统观察不了"**，显示"未就绪"是诚实的。

### 19.3 如何证明"不是显示修好了、实际逻辑没修"

代码层面的证据：

1. **根因修改点在 LabelGenerator 而不是 Display**：`_compute_anchor_health()` 是产生 None 的**唯一**源头；Display 只是忠实展示 None。v1.3.4 动的是源头。
2. **快照字段连带修复**：`anchor_health_score` / `anchor_health_level` 写入 labels 表。修复后这两个字段在符合条件时会有非 None 值。回放历史快照就能对比：v1.3.3 快照里这俩字段 3 小时全 None，v1.3.4 部署后应当从 GEX 首次成功时起就有值。
3. **行为烟测已证**（本轮代码验证阶段已完成）：
   - 场景 A：GEX 从未成功 → score=None / level=未就绪 ✓
   - 场景 B：anchor_state=EXPIRED → score=None / level=未就绪 ✓
   - 场景 C（本次核心修复场景）：COLD_START + GEX FRESH + 因子有值 → **score=32.2 / level=警** ✓
   - 场景 D：所有因子 None 但 GEX fresh → score=25.0 / level=危（各因子走 0.5）✓
   - 场景 E：CONFIRMED + centrality_frozen 期 → score 能算（基于透出的最后有效值）✓

### 19.4 部署后即可验证

修复后部署，**预期第一个 health 分数出现的时间**是：GEX 首次 fetch 成功后的下一次 `tick_status` 触发。在正常网络条件下：

- 系统启动 → 主循环第 1 轮（~2 秒）→ `anchor_ctx.check_update()` 发起 GEX fetch
- GEX fetch 成功（通常 < 5 秒）→ `_last_fetch_success_ms` 非 0，`anchor_state = FRESH`
- 下一个 tick_status 触发（≤ 5 秒）→ `_render_status` 调用 `label_gen.generate` → `_compute_anchor_health` 计算得分 → 状态栏《带内结构健康评估》行 1 显示 `XX.X / 等级`

**预期 60 秒内看到首个 health 分数**。若 60 秒后仍显示"未就绪"，说明 GEX 接口异常，此时状态栏《数据采集》行 3「GEX 采集」会显示「上次成功 -」或超过 1 分钟未更新，这是诚实的"未就绪"。

---

*文档完。v1.3.4 锚健康度可用性语义收口 + 减冗清理。*
