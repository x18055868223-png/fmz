# -*- coding: utf-8 -*-
"""
Alpha Radar v1.3.4  (FMZ 单文件策略)
================================================================
研究型价格结构观测系统。无任何实盘发单逻辑。
蓝图: alpha_radar_spec v1.3.4 / 参考: gamma_spatial_observer_v6.py

四大纪律:
  1. 所有速度/动能/波动率基于等币量柱(Volume Bars)，不用墙上时钟
  2. band_half 由去趋势波动率×tanh 推导，不受 API 网格间距约束
  3. aggTrades 数据断层只 Log 警告，不清空任何历史窗口
  4. 无魔法数字，变量全称，禁压缩语法

v1.1 修复:
  - reset_ppe_history 指令激活
  - 吸收趋势冻结语义完整化
  - OLS 窗口在 gap_closure_event 后清空

v1.2 修复:
  - 去趋势 σ_slow + 相对价格硬护栏
  - Chart Flag 重构为事件驱动 + 置信度过滤
  - 事件本地持久化 (jsonl 追加写)
  - 状态栏新增"高价值事件流"表

v1.3 新增 (带内结构健康观测层，纯观测不参与门控):
  - ClassificationEvidence 新增中心性原始因子
  - LabelGenerator 新增乘法式 anchor_health_score
  - 状态栏新增《带内结构健康评估》独立表

v1.3.1 外科式修补: 6 项外科式修补

v1.3.2 时效性防御 + 展示语义收口 + 健康度观测出口:
  E-1 BarAssembler.poll_with_drain() 三道护栏
  E-2 Display 缺值语义三态收口
  E-3 LogProfit 改输出 anchor_health_score
  E-4 raw_values +2 字段 (poll_wall_time_ms / catchup_rounds_used)
  E-5 状态栏《采集时效性》2 行表

v1.3.3 任务调度边界收口 + 数据采集状态栏增强:
  T-1 time-driven / event-driven 任务划分
  T-2 Display latest_* 缓存 + pending_chart_queue
  T-3 chart 原语拆分
  T-4 fmt_datetime_ms_utc8 helper
  T-5 《采集时效性》→《数据采集》5 行表
  T-6 AnchorContext / BarAssembler 补计数器与 getter

v1.3.4 锚健康度可用性收口 + 减冗清理:
  H-1 _compute_anchor_health 去掉 runtime_gate == COLD_START 一票否决
      根因: v1.3 首版把 "全局 PPE 分布就绪 (400 柱)" 绑成了
            "锚健康度可见" 的前置门槛, 导致 3 小时 300 柱实盘运行
            下 ED1/center_loss 都已有值却仍长期显示未就绪。
      改法: readiness 语义从"全局系统就绪"降级为"锚层自身就绪"。
            health=None 的合法触发收敛为两种:
              (a) anchor_source_ts_ms 缺失或为 0 (GEX 从未成功过)
              (b) anchor_state == EXPIRED (锚参照系已失效)
            其他情况 health 可计算; 各因子缺值走 0.5 中性, 沿用
            规范 §13.3 已定的缺值策略。
  H-2 顺带清理审计剩余冗余 (上轮减冗审计 R-1..R-9):
      - 删 3 个 CONFIG 死键: absorption_trend_window /
        ppe_high_res_pct / ppe_low_res_pct
      - 删 raw_evidence return dict 中的冗余字段 cvd_sum
        (内部仍作为局部变量用于 direction 判定, 只是不再向外透出)
      - 删零调用 getter: BarAssembler.get_current_price /
        AnchorContext.get_last_fetch_attempt_ms
      - 清理 ~15 处老版本编号前缀 Q1/Q2/Q3/Q4/Q5 修正 /
        额外提醒一二修正 / 痛点 1/3 (保留修正内容, 仅删纯编号)
      - 删 _render_status docstring 里 "(原 _update_status)" 历史引用
================================================================
"""

import json
import time
import math
import ssl
import datetime
import traceback
import collections
import urllib.request
import urllib.error
from urllib.parse import urlencode


# ================================================================
# SECTION 0: CONFIG
# K 必须在 CONFIG 之前定义，供 CONFIG 内的派生窗口使用
# ================================================================

K = 20  # 基础窗口单位 (等币量柱数)，所有窗口均从此派生

CONFIG = {
    # ── GEX API (AnchorContext) ──────────────────────────────────
    "gex_base_url":                 "https://gexmonitor.com/api/gex-latest",
    "gex_asset":                    "BTC",
    "gex_exchange":                 "all",
    "gex_lite":                     "true",
    "gex_min_fetch_interval_ms":    60_000,
    # 刷新频率不固定，60 分钟内数据视为可用
    "gex_freshness_stale_ms":       180_000,    # 3 分钟: FRESH → STALE
    "gex_freshness_expired_ms":     3_600_000,  # 60 分钟: STALE → EXPIRED
    "gex_http_timeout_sec":         5,
    "gex_http_retries":             2,
    "gex_http_retry_delays":        [0.6, 1.2],

    # ── 锚偏移事件 ───────────────────────────────────────────────
    # v1.3.1 Fix-6: 拆分为两档阈值，补全 v1.3 未拆开的响应分层。
    #   anchor_shift_frac     : 轻档，触发 RESETTING + OLS + deviation 重置
    #   anchor_ppe_reset_frac : 重档，在轻档基础上额外触发 PPE 历史重置
    # 约束: anchor_ppe_reset_frac >= anchor_shift_frac (否则无意义，
    #       启动时由 validate_config() 强制检查)。
    "anchor_shift_frac":            0.5,  # flip_point 移动 > 此比例×band_half 触发 RESETTING
    "anchor_ppe_reset_frac":        1.0,  # flip_point 移动 > 此比例×band_half 额外清空 PPE 历史
    "anchor_stable_bars":           K,    # RESETTING → FRESH 需要的稳定柱数

    # ── Binance aggTrades REST ───────────────────────────────────
    "binance_url":                  "https://api.binance.com/api/v3/aggTrades",
    "binance_symbol":               "BTCUSDT",
    "agg_trades_limit":             1000,
    "binance_http_timeout_sec":     5,

    # ── CVD 数据完整性 (REST 增强) ──────────────────────────
    # REST 轮询在高流速下可能丢失成交（单次上限 1000 条），
    # 当检测到 trade_id 缺口时，CVD 降级为不可信，强度门强制归零。
    # 降级在下一次无缺口的完整轮询后自动恢复。
    "cvd_gap_degrade_enabled":      True,

    # ── 等币量柱 ─────────────────────────────────────────────────
    "volume_bar_n":                 10.0,   # BTC/柱

    # ── v1.3.2: Catch-up Drain (BarAssembler.poll_with_drain) ───
    # 解决主循环慢 + REST pull 模型下的 backlog 问题。
    # 单轮主循环允许 BarAssembler 连续 poll 多次直到追上市场尾部或触顶。
    # 三道护栏:
    #   (A) drain_enabled=False 时退化为单次 poll (v1.3.1 行为)
    #   (B) max_drain_rounds     — poll 次数上限
    #   (C) max_drain_wall_time_ms — 挂钟累计耗时上限
    # 护栏 B 避免"市场比 REST 快时无限循环"；
    # 护栏 C 避免"单次 drain 挤占 AnchorContext / Display 的 CPU 份额"。
    # 此外还有隐式护栏: poll 返回 < agg_trades_limit 说明已追上队尾。
    "drain_enabled":                True,
    "max_drain_rounds":             5,     # 5 × 1000 = 5000 条覆盖 ≈ 5 分钟 100 笔/分钟流速
    "max_drain_wall_time_ms":       3000,  # 3 秒上限，loop_sleep_sec=2 下留 1 秒净开销
    "drain_log_interval_sec":       60,    # drain 摘要日志最小间隔，防刷屏
    "backlog_warn_interval_sec":    30,    # backlog 告警最小间隔

    # ── 窗口大小 (均从 K 派生，修改 K 即可全局生效) ──────────────
    "K":                            K,
    "cvd_window":                   K,
    "ppe_short_window":             K,
    "ols_window":                   3 * K,      # 60 根 outside 柱
    "ppe_history_window":           20 * K,     # 400 根柱 ≈ 5~6 小时

    # ── 吸收带公式 ───────────────────────────────────────────────
    "band_base_sigma":              3.0,
    "band_max_sigma_bonus":         3.0,
    "band_spring_midpoint":         5.0,    # tanh 分母 [待校准]
    "band_fallback_half_pct":       0.005,  # std 不可用时的降级带宽 (0.5% 价格)
    # v1.2: band_half 相对价格的硬上限（去趋势后仍异常时的兜底护栏）
    "band_half_max_pct":            0.015,  # 1.5% of price，超出则 clamp + 记录

    # ── 事件持久化 (v1.2) ───────────────────────────────────────
    # 追加写入高价值事件到本地 jsonl 文件。写失败只 WARN，不影响主循环。
    "event_persist_enabled":        True,
    "event_persist_path":           "/tmp/alpha_radar_events.jsonl",

    # ── 事件流展示 (v1.2) ───────────────────────────────────────
    "event_stream_display_size":    5,      # 状态栏显示最近 N 条事件

    # ── 脱离判断 ─────────────────────────────────────────────────
    "deviation_threshold":          1.0,    # |归一化偏差| >= 1 = outside
    "outside_bar_confirm":          2,      # 确认脱离所需连续 outside 柱 [待校准]
    "inside_bar_confirm":           2,      # 确认缺口闭合所需连续 inside 柱 (Q3)

    # ── PPE ──────────────────────────────────────────────────────
    "ppe_spike_mult":               1.5,    # bar 振幅 > N×band_half = 尖峰异常
    # 分类层: ppe_quality 基于 ppe_raw 做阈值映射 (规范 8.8 节)
    "ppe_quality_high_resistance":  0.30,   # ppe_raw < 此值 = 高阻力
    "ppe_quality_low_resistance":   0.70,   # ppe_raw > 此值 = 低阻力

    # ── OLS ──────────────────────────────────────────────────────
    "ols_r2_min":                   0.30,   # R² 低于此值 = 斜率无效 [待校准]
    "ols_exp_thresh":               0.05,   # 斜率 > 此值 = 扩张 [待校准]
    "ols_con_thresh":               -0.05,  # 斜率 < 此值 = 收缩 [待校准]
    "ols_min_bars":                 3 * K,  # 运行 OLS 所需最少 outside 柱数 (= 3K，规范 7.7)

    # ── CVD ──────────────────────────────────────────────────────
    "cvd_strength_gate":            0.20,   # 强度 < 此值 = neutral [待校准]

    # ── 吸收趋势标签阈值 ─────────────────────────────────────────
    # PPE 上升 = 路径效率升高 = 吸收减弱 = 锚承压 (规范 7.4)
    # PPE 下降 = 路径效率降低 = 吸收增强 = 锚修复 (规范 7.4)
    "absorption_trend_stress_slope":    0.005,  # PPE 斜率 > 此值 = 锚承压中
    "absorption_trend_recover_slope":  -0.005,  # PPE 斜率 < 此值 = 锚修复中

    # ── v1.3: 带内中心性 (ClassificationEvidence 内部计算) ────
    # 窗口与 OLS 对齐 (3K); 半衰期复用 K; d_cap 截断防止 CONFIRMED
    # 期极端偏离污染带内健康模型。
    "centrality_window":                3 * K,  # 缓冲区大小
    "centrality_min_bars":              K,      # 最少柱数，少于此值输出 None
    "centrality_ewma_halflife":         K,      # 指数加权半衰期 (根 Bar)
    "centrality_d_cap":                 1.5,    # normalized_deviation 截断上限
    "centrality_sign_eps":              0.10,   # 方向一致性的死区阈值

    # ── v1.3: erosion_side 标签 ───────────────────────────────
    "erosion_side_threshold":           0.30,   # |ED1| > 此值 → 标注侵蚀方向

    # ── v1.3: 健康度 (LabelGenerator 聚合) ────────────────────
    # 所有参数为观测模型初值，待实盘校准。不参与任何状态转换。
    "health_erosion_inflection":        0.60,   # H_erosion sigmoid 中心
    "health_erosion_beta":              8.0,    # H_erosion sigmoid 陡度
    "health_center_loss_inflection":    0.80,   # H_center_loss sigmoid 中心
    "health_center_loss_beta":          8.0,    # H_center_loss sigmoid 陡度
    "health_micro_beta":                6.0,    # H_micro sigmoid 陡度
    "health_time_fresh_floor":          0.90,   # FRESH 期末端 H_time 下限

    # ── v1.3: 健康度分档 (仅 UI，不进入决策) ────────────────
    "health_level_healthy":             80.0,   # ≥ 80 → "优"
    "health_level_solid":               60.0,   # 60-80 → "良"
    "health_level_stressed":            40.0,   # 40-60 → "警"
    "health_level_critical":            20.0,   # 20-40 → "危"; < 20 → "濒危"

    # ── 展示 ─────────────────────────────────────────────────────
    # v1.3.3: 以下 *_interval_sec 全部为 "固定时间任务" 调度参数,
    # 由 run() 级别的调度器周期性触发, 不再通过 on_bar() 间接触发。
    "chart_update_interval_sec":    10,    # chart 线条刷新周期 (time-driven)
    "status_update_interval_sec":   5,     # 状态栏刷新周期 (time-driven, v1.3.3 新增)
    "logprofit_interval_sec":       10,    # LogProfit 输出周期 (time-driven)
    "summary_log_interval_sec":     60,    # 综述日志周期 (time-driven)
    "snapshot_history_size":        500,

    # ── 主循环 ───────────────────────────────────────────────────
    "loop_sleep_sec":               2.0,
}


def validate_config():
    """
    v1.3.1 Fix-6: 启动时配置自检。
    当前强制约束:
      1) anchor_ppe_reset_frac >= anchor_shift_frac
         (PPE 历史重置必须在 RESETTING 的更高阈值上，否则两档响应坍缩为一档)
      2) health_level_* 四档阈值单调递减 (UI 分档完整性)

    违反硬约束直接 raise，避免运行时出现逻辑倒挂。
    """
    shift_frac      = CONFIG["anchor_shift_frac"]
    ppe_reset_frac  = CONFIG["anchor_ppe_reset_frac"]
    if ppe_reset_frac < shift_frac:
        raise ValueError(
            "CONFIG invariant violated: "
            "anchor_ppe_reset_frac ({}) must be >= anchor_shift_frac ({})".format(
                ppe_reset_frac, shift_frac))

    levels = [
        CONFIG["health_level_healthy"],
        CONFIG["health_level_solid"],
        CONFIG["health_level_stressed"],
        CONFIG["health_level_critical"],
    ]
    for idx in range(len(levels) - 1):
        if levels[idx] <= levels[idx + 1]:
            raise ValueError(
                "CONFIG invariant violated: "
                "health_level thresholds must be strictly descending, got {}".format(
                    levels))


# 模块加载时立即自检，错配直接暴露，不等到运行期。
validate_config()


# ================================================================
# SECTION 1: UTILITY FUNCTIONS
# ================================================================

def now_ms():
    """当前 UTC 时间（毫秒）。"""
    return int(time.time() * 1000)


def safe_float(value):
    """转 float；None、非有限值、类型错误均返回 None。"""
    try:
        if value is None:
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def safe_int(value):
    """转 int；None、bool、类型错误均返回 None。"""
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_iso_to_ms(text):
    """ISO 8601 时间字符串 → 毫秒时间戳。"""
    if not text:
        return None
    try:
        raw = str(text).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _get_fmz_function(name):
    """获取 fmz 平台内置函数；本地测试时返回 None 并降级到 print。"""
    fn = globals().get(name)
    return fn if callable(fn) else None


def log_info(message):
    fn = _get_fmz_function("Log")
    if fn:
        fn(str(message))
    else:
        print(str(message))


def log_warn(message):
    log_info("[WARN] " + str(message))


def log_error(message):
    log_info("[ERROR] " + str(message))


def log_status(summary_text, tables=None):
    """
    输出 fmz 状态栏。
    tables 为 list[dict]，通过 backtick 特殊格式嵌入（fmz 约定）。
    """
    try:
        payload = str(summary_text)
        if tables:
            payload += "\n`" + json.dumps(tables, ensure_ascii=False) + "`"
        fn = _get_fmz_function("LogStatus")
        if fn:
            fn(payload)
        else:
            print(payload)
    except Exception as error:
        log_warn("log_status failed: " + str(error))


def fmt_price(value):
    v = safe_float(value)
    return "{:.2f}".format(v) if v is not None else "-"


def fmt_number(value, decimals=3):
    v = safe_float(value)
    if v is None:
        return "-"
    return ("{:." + str(decimals) + "f}").format(v)


def fmt_percent(value):
    v = safe_float(value)
    return "{:+.3f}%".format(v * 100.0) if v is not None else "-"


def fmt_timestamp_ms(milliseconds):
    """毫秒时间戳 → UTC+8 HH:MM:SS 字符串。"""
    ms = safe_int(milliseconds)
    if ms is None:
        return "-"
    try:
        dt = (datetime.datetime.utcfromtimestamp(ms / 1000.0)
              + datetime.timedelta(hours=8))
        return dt.strftime("%H:%M:%S")
    except Exception:
        return "-"


def fmt_datetime_ms_utc8(milliseconds):
    """
    v1.3.3 新增: 毫秒时间戳 → UTC+8 YYYY-MM-DD HH:MM:SS。

    用途: 状态栏"上次数据获取时间"类字段需要完整日期，
         不能只给 HH:MM:SS（跨日时观察者无法辨别是今天还是昨天）。

    缺值时返回 "-"，不伪造日期。
    与 fmt_timestamp_ms 并存: 事件流等场合仍使用紧凑的 HH:MM:SS。
    """
    ms = safe_int(milliseconds)
    if ms is None:
        return "-"
    try:
        dt = (datetime.datetime.utcfromtimestamp(ms / 1000.0)
              + datetime.timedelta(hours=8))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def http_get_json(url, timeout_sec=5, retries=2):
    """
    带重试的 HTTP GET，返回 (dict, error_string)。
    fmz Python 不支持 HttpQuery，使用标准库 urllib。
    """
    headers = {
        "User-Agent":    "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0",
        "Accept":        "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
        "Referer":       "https://gexmonitor.com/",
    }
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    retry_delays = CONFIG.get("gex_http_retry_delays", [0.6, 1.2])
    last_error = None

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url=url, headers=headers, method="GET")
            resp = opener.open(req, timeout=timeout_sec)
            status = safe_int(getattr(resp, "status", 200))
            if status is not None and status != 200:
                raise RuntimeError("http_status_" + str(status))
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                return payload, None
            raise RuntimeError("response_is_not_dict")
        except Exception as error:
            last_error = str(error)
            if attempt < retries:
                time.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])

    return None, last_error


def ols_slope_and_r2(values):
    """
    OLS 线性回归: y=values, x=[0,1,...,n-1]。
    返回 (slope, r_squared)；数据不足时返回 (None, None)。

    R² 可为负值（拟合比水平均值线还差），这是正确行为——
    表示路径高度非线性，应触发 R² 门控使斜率无效。

    纪律1: 此函数只应在等币量柱序列上调用，不在墙上时钟序列上调用。
    """
    if not values or len(values) < 3:
        return None, None

    ys = [safe_float(v) for v in values]
    if any(v is None for v in ys):
        return None, None

    n = len(ys)
    mean_x = (n - 1) / 2.0
    mean_y = sum(ys) / n
    cov_xy = 0.0
    var_x = 0.0

    for i in range(n):
        dx = i - mean_x
        cov_xy += dx * (ys[i] - mean_y)
        var_x += dx * dx

    if var_x == 0.0:
        return 0.0, None  # 完全水平，斜率为 0，R² 无意义

    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x

    ss_res = sum((ys[i] - (slope * i + intercept)) ** 2 for i in range(n))
    ss_tot = sum((ys[i] - mean_y) ** 2 for i in range(n))

    if ss_tot == 0.0:
        return slope, 1.0  # 所有值相同，完美拟合

    return slope, 1.0 - ss_res / ss_tot


def std_dev_population(values):
    """总体标准差。数据不足时返回 None。"""
    if not values or len(values) < 2:
        return None
    vs = [safe_float(v) for v in values]
    if any(v is None for v in vs):
        return None
    mean = sum(vs) / len(vs)
    variance = sum((v - mean) ** 2 for v in vs) / len(vs)
    return math.sqrt(variance)


def detrended_std_population(values):
    """
    去趋势标准差 (v1.2)。

    对输入序列做 OLS 线性拟合，计算残差序列的总体标准差。
    物理意义: 度量"在当前趋势之上还剩多少震荡"，
    消除单边行情下方向性位移对波动率度量的污染。

    设计动机:
      当价格呈单调趋势时，原始 std 反映的是"方向性位移"而非"震荡幅度"，
      导致 band_half 膨胀，脱离事件被无限变宽的防线自动吸收。
      去趋势后:
        - 震荡行情: 拟合斜率 ≈ 0，残差 ≈ 原始偏差，退化为普通 std
        - 趋势行情: 拟合带走方向分量，残差小，std 反映真实震荡

    返回:
      残差序列标准差（float），数据不足时返回 None。
    """
    if not values or len(values) < 3:
        return None
    ys = [safe_float(v) for v in values]
    if any(v is None for v in ys):
        return None

    n = len(ys)
    mean_x = (n - 1) / 2.0
    mean_y = sum(ys) / n
    cov_xy = 0.0
    var_x = 0.0

    for i in range(n):
        dx = i - mean_x
        cov_xy += dx * (ys[i] - mean_y)
        var_x += dx * dx

    if var_x == 0.0:
        # 完全水平序列，退化为普通 std
        return std_dev_population(values)

    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x

    residuals = [ys[i] - (slope * i + intercept) for i in range(n)]
    residual_mean = sum(residuals) / n
    residual_var = sum((r - residual_mean) ** 2 for r in residuals) / n

    return math.sqrt(residual_var)


def percentile_rank(value, history_values):
    """
    经验 CDF 排位: history 中 <= value 的比例，返回 [0.0, 1.0]。
    此函数在 20K 柱历史上计算，结果非常平滑，
    不依赖单根柱的 raw PPE，不存在过度抖动问题。
    """
    if value is None or not history_values:
        return None
    count_at_or_below = sum(1 for h in history_values if h <= value)
    return count_at_or_below / len(history_values)


def median_of(values):
    """序列中位数。空序列返回 None。"""
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    sorted_vals = sorted(valid)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 == 1:
        return sorted_vals[mid]
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


# ================================================================
# SECTION 2: MODULE 1 — AnchorContext
# ================================================================

class AnchorContext:
    """
    从 gexmonitor.com 轮询 GEX 快照，计算吸收带锚点参数。

    主要输出:
      flip_point       GEX 定价锚价格
      spring           hedging_curve 在 flip 处的 |dH/dP| (BTC/USD)
      band_half        σ_slow × sigma_count（需 BarAssembler 提供 std_usd）

    新鲜度三态 (60 分钟内均视为可用):
      FRESH   age < 3 min     全置信度
      STALE   3 min ≤ age < 60 min  置信度降级但不阻断
      EXPIRED age ≥ 60 min    下游 UNAVAILABLE

    band_half 物理推导 (纪律1, 纪律2):
      capacity_per_sigma = spring × std_usd / volume_bar_n
        物理含义: 价格偏离 1σ 时，做市商需要对冲多少个 volume bar 的量
      sigma_count = base(3) + bonus(3) × tanh(capacity / midpoint(5))
        弱弹簧 → sigma_count ≈ 3；强弹簧 → sigma_count → 6（tanh 自然饱和）
      band_half = std_usd × sigma_count

    API 网格间距永远不参与 band_half 计算（纪律2）。
    """

    FRESH   = "FRESH"
    STALE   = "STALE"
    EXPIRED = "EXPIRED"

    def __init__(self):
        self._last_fetch_attempt_ms = 0
        self._last_valid_gex        = None
        self._prev_flip_point       = None
        self._prev_band_half        = None
        self._band_clamped          = False  # v1.2: 最近一次 compute_band_half 是否触发硬护栏

        # v1.3.3: 采集计数器 (供状态栏"GEX 采集"行消费)
        # 与 _last_fetch_attempt_ms 不同, 本字段语义更明确:
        #   _last_fetch_attempt_ms  : 上次 HTTP 调用发起时刻 (不管成功与否, 用于节流判断)
        #   _last_fetch_success_ms  : 上次 HTTP 调用成功解析到 GEX 快照的时刻
        # 两者拆开后, 状态栏可以区分"在尝试但失败"与"完全没在尝试"。
        self._last_fetch_success_ms       = 0
        self._fetch_attempt_count_total   = 0
        self._fetch_success_count_total   = 0
        self._fetch_attempted_this_cycle  = False   # 本轮主循环是否触发了一次 fetch 尝试

    def check_update(self):
        """
        每个主循环 tick 调用一次。
        内部节流至 gex_min_fetch_interval_ms，无需外部控制。

        v1.3.3:
          _fetch_attempted_this_cycle 每轮主循环开始时由外部 clear_cycle_flag()
          清零, 本方法在真正触发 fetch 时置 True。
          这样状态栏可以显示"本轮是否触发了 GEX 调用"(0/1)。
        """
        current_ms = now_ms()
        if (current_ms - self._last_fetch_attempt_ms) >= CONFIG["gex_min_fetch_interval_ms"]:
            self._last_fetch_attempt_ms = current_ms
            self._fetch_attempt_count_total += 1
            self._fetch_attempted_this_cycle = True
            self._try_fetch(current_ms)

    def clear_cycle_flag(self):
        """v1.3.3: 由 run() 在每轮主循环开始时调用, 清零本轮 fetch 触发标记。"""
        self._fetch_attempted_this_cycle = False

    def _try_fetch(self, current_ms):
        url = (CONFIG["gex_base_url"] + "?"
               + urlencode({
                   "asset":    CONFIG["gex_asset"],
                   "exchange": CONFIG["gex_exchange"],
                   "lite":     CONFIG["gex_lite"],
                   "t":        current_ms,
               }))
        payload, error = http_get_json(
            url,
            timeout_sec=CONFIG["gex_http_timeout_sec"],
            retries=CONFIG["gex_http_retries"],
        )
        if payload is None:
            log_warn("AnchorContext: GEX fetch failed — " + str(error))
            return
        parsed = self._parse_payload(payload)
        if parsed is not None:
            self._last_valid_gex = parsed
            # v1.3.3: 仅在 payload 解析成功且产生可用 GEX 快照时才计成功
            # (HTTP 200 但 schema 不合法不算成功)
            self._last_fetch_success_ms      = now_ms()
            self._fetch_success_count_total += 1
        else:
            log_warn("AnchorContext: GEX payload parse failed")

    def _parse_payload(self, payload):
        flip_point = safe_float(payload.get("flip_point"))
        if not flip_point or flip_point <= 0:
            return None

        source_ts_ms = self._extract_timestamp(payload)
        if source_ts_ms is None:
            return None

        asset_price = safe_float(payload.get("asset_price"))

        # hedging_curve 在 lite 模式下可能缺失，graceful fallback → spring=0
        hedging_flows = payload.get("hedging_flows") or {}
        hedging_curve = hedging_flows.get("hedging_curve") or []
        spring = self._extract_spring_from_curve(hedging_curve, flip_point)

        return {
            "flip_point":   flip_point,
            "asset_price":  asset_price,
            "source_ts_ms": source_ts_ms,
            "spring":       spring,
        }

    def _extract_timestamp(self, payload):
        """多路径降级解析时间戳。"""
        ts_raw = payload.get("timestamp")
        if ts_raw:
            if isinstance(ts_raw, (int, float)) and ts_raw > 1e12:
                return safe_int(ts_raw)
            result = parse_iso_to_ms(ts_raw)
            if result:
                return result

        # 降级: 从任意 profile 的 meta.updateTime 提取
        profiles = payload.get("profiles") or {}
        for _key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            meta = profile.get("meta") or {}
            update_time = meta.get("updateTime")
            if update_time:
                result = parse_iso_to_ms(update_time)
                if result:
                    return result
        return None

    def _extract_spring_from_curve(self, hedging_curve, flip_point):
        """
        spring = |dH/dP| at the segment containing flip_point.
        H = 做市商净对冲量 (BTC)，P = 价格 (USD)。
        物理含义: 每 1 USD 价格变动，做市商需对冲多少 BTC。

        hedging_curve 缺失（lite 模式）→ spring=0.0
        spring=0 时: capacity_per_sigma=0, sigma_count=base_sigma=3，
        band_half = std_usd × 3（最小吸收力假设，非魔法数字）。
        """
        if not isinstance(hedging_curve, list) or len(hedging_curve) < 2:
            return 0.0

        nodes = []
        for item in hedging_curve:
            price = safe_float(item.get("price"))
            hedging_btc = safe_float(item.get("hedging_btc"))
            if price is not None and hedging_btc is not None:
                nodes.append((price, hedging_btc))

        if len(nodes) < 2:
            return 0.0

        nodes.sort(key=lambda node: node[0])
        fp = safe_float(flip_point)
        if fp is None:
            return 0.0

        # 找到包含 flip_point 的区间
        for i in range(1, len(nodes)):
            price_lo, hedging_lo = nodes[i - 1]
            price_hi, hedging_hi = nodes[i]
            if price_lo <= fp <= price_hi:
                delta_price = price_hi - price_lo
                if delta_price > 0:
                    return abs(hedging_hi - hedging_lo) / delta_price
                return 0.0

        # 降级: 用离 flip_point 最近的两个节点估算
        closest_nodes = sorted(nodes, key=lambda node: abs(node[0] - fp))[:2]
        if len(closest_nodes) == 2:
            delta_price = abs(closest_nodes[1][0] - closest_nodes[0][0])
            if delta_price > 0:
                return abs(closest_nodes[1][1] - closest_nodes[0][1]) / delta_price

        return 0.0

    def compute_band_half(self, std_usd, current_price):
        """
        给定 BarAssembler 提供的 std_usd（等币量时间波动率），计算 band_half。
        必须在 check_update() 之后调用。

        纪律1: std_usd 必须来自 Volume Bar 序列的去趋势标准差。
        纪律2: API 网格间距不参与此计算。

        v1.2 修正:
          叠加相对价格硬护栏。std_usd 已由 BarAssembler 去趋势化，
          99% 的场景下带宽回归正常。本护栏作为 last-resort 兜底，
          捕获极端数据污染或 GEX spring 异常高的情况。
          触发时记录 _band_clamped 状态供下游审计。
        """
        self._band_clamped = False  # 每次调用重置

        if self._last_valid_gex is None:
            return None

        spring       = self._last_valid_gex["spring"]
        flip_point   = self._last_valid_gex["flip_point"]
        base_sigma   = CONFIG["band_base_sigma"]
        max_bonus    = CONFIG["band_max_sigma_bonus"]
        midpoint     = CONFIG["band_spring_midpoint"]
        volume_bar_n = CONFIG["volume_bar_n"]
        fallback_pct = CONFIG["band_fallback_half_pct"]
        max_band_pct = CONFIG["band_half_max_pct"]

        price_ref = current_price if (current_price and current_price > 0) else flip_point
        if not price_ref or price_ref <= 0:
            return None

        if std_usd is not None and std_usd > 0:
            working_std_usd = std_usd
        else:
            # 降级模式: band_half = fallback_pct × price
            # 等价于 working_std = fallback_pct × price / base_sigma，
            # 使 sigma_count=3 时 band_half = fallback_pct × price。
            working_std_usd = price_ref * fallback_pct / base_sigma

        if spring > 0 and working_std_usd > 0:
            capacity_per_sigma = spring * working_std_usd / volume_bar_n
        else:
            capacity_per_sigma = 0.0

        sigma_count = base_sigma + max_bonus * math.tanh(capacity_per_sigma / midpoint)
        raw_band_half = working_std_usd * sigma_count

        # 工程下限: 防止 std 异常小时带宽归零。
        # 0.1% 价格 ≈ $68（@$68k BTC），来自"不应被 tick 噪声触发"的工程判断。
        minimum_band_half = price_ref * 0.001

        # v1.2 工程上限: 相对价格硬护栏
        # 去趋势 σ_slow 已处理了绝大多数单边污染，本上限只在极端情况触发。
        maximum_band_half = price_ref * max_band_pct

        bounded_band_half = max(raw_band_half, minimum_band_half)
        if bounded_band_half > maximum_band_half:
            self._band_clamped = True
            log_warn(
                "AnchorContext: band_half clamped "
                "raw={:.1f} → max={:.1f} ({:.2f}% of price={:.1f})".format(
                    bounded_band_half, maximum_band_half,
                    max_band_pct * 100, price_ref))
            bounded_band_half = maximum_band_half

        return bounded_band_half

    def was_band_clamped(self):
        """v1.2: 返回本次 compute_band_half 是否触发了硬护栏。"""
        return self._band_clamped

    def detect_anchor_shift(self, current_flip_point, current_band_half):
        """
        检测 flip_point 是否移动超过 anchor_shift_frac × band_half。
        返回 (shift_event: bool, shift_magnitude: float or None)。

        (规范 8.4):
          shift_magnitude 返回相对量 = |位移| / band_half，
          不是绝对美元位移。这样 SSM 的 > 0.5 判定和日志语义
          与规范定义一致。

        每柱调用一次。
        """
        if (current_flip_point is None
                or current_band_half is None
                or current_band_half <= 0):
            return False, None

        if self._prev_flip_point is None:
            self._prev_flip_point = current_flip_point
            self._prev_band_half  = current_band_half
            return False, None

        absolute_shift = abs(current_flip_point - self._prev_flip_point)
        relative_shift = absolute_shift / current_band_half
        shift_threshold = CONFIG["anchor_shift_frac"]
        shift_occurred  = relative_shift > shift_threshold

        self._prev_flip_point = current_flip_point
        self._prev_band_half  = current_band_half

        return shift_occurred, (relative_shift if shift_occurred else None)

    def get_flip_point(self):
        return self._last_valid_gex["flip_point"] if self._last_valid_gex else None

    def get_freshness(self):
        if self._last_valid_gex is None:
            return self.EXPIRED
        age_ms = now_ms() - self._last_valid_gex["source_ts_ms"]
        if age_ms < CONFIG["gex_freshness_stale_ms"]:
            return self.FRESH
        if age_ms < CONFIG["gex_freshness_expired_ms"]:
            return self.STALE
        return self.EXPIRED

    def get_source_ts_ms(self):
        return self._last_valid_gex["source_ts_ms"] if self._last_valid_gex else None

    def get_spring(self):
        return self._last_valid_gex["spring"] if self._last_valid_gex else 0.0

    # ── v1.3.3: 采集计数器 getter ────────────────────────────────
    def get_last_fetch_success_ms(self):
        """上次成功解析到 GEX 快照的时刻。0 表示从未成功。"""
        return self._last_fetch_success_ms

    def get_fetch_attempt_count_total(self):
        """累计 HTTP 调用次数。"""
        return self._fetch_attempt_count_total

    def get_fetch_success_count_total(self):
        """累计成功解析次数。"""
        return self._fetch_success_count_total

    def was_fetch_attempted_this_cycle(self):
        """本轮主循环是否触发了一次 fetch 尝试 (0/1 用于状态栏)。"""
        return self._fetch_attempted_this_cycle


# ================================================================
# SECTION 3: MODULE 2 — BarAssembler
# ================================================================

class BarAssembler:
    """
    将 Binance aggTrades REST 响应聚合为等币量柱 (Volume Bars)。

    Volume Time (纪律1):
      每根柱代表恰好 volume_bar_n BTC 的成交量，
      消除了 REST 轮询时间不均匀性，是所有下游计算的时间坐标。

    OHLC 跟踪:
      v6 只记录 close 价格，Alpha Radar 增加 open/high/low，
      用于 PPE 计算 (|close-open|/(high-low))。

    数据断层处理 (纪律3):
      发现 trade_id 不连续时，只打 WARN 日志，绝不清空已积累的历史。
      历史波动率基线是宝贵的，不能因网络抖动而丢弃。

    CVD 符号约定 (Binance aggTrades):
      m=False → 买方主动 (taker buy)  → signed_qty = +qty（正向 CVD 贡献）
      m=True  → 卖方主动 (taker sell) → signed_qty = -qty（负向 CVD 贡献）
    """

    def __init__(self):
        self._last_trade_id    = None
        self._last_trade_price = None

        # 当前未完成柱的聚合状态
        self._current_open   = None
        self._current_high   = None
        self._current_low    = None
        self._current_close  = None
        self._current_volume = 0.0
        self._current_cvd    = 0.0

        # 已完成柱的环形缓冲区
        # 大小: ppe_history_window 已由 ClassificationEvidence 内部维护，
        # 这里只需要足够 get_slow_std_usd() 使用 (K*3 根)，加少量余量。
        max_bars_for_std = CONFIG["K"] * 3 + 20
        self._completed_bars = collections.deque(maxlen=max_bars_for_std)
        self._bar_index      = 0

        # REST CVD 增强: trade_id 缺口降级标记
        # REST 单次上限 1000 条，高流速下可能丢失成交，CVD 不可信。
        # 检测到缺口时标记降级，下一次无缺口轮询自动恢复。
        self._cvd_gap_degraded = False

        # v1.3.2: 采集度量 (catch-up drain 副产物)
        # 这些字段在每轮 poll_with_drain() 结束时刷新，供 Display 消费。
        # 不入快照表（除了 _last_cycle_wall_time_ms 和 _last_cycle_catchup_rounds
        # 通过 getter 暴露给 run() 写入 raw_values）。
        self._last_poll_returned_count  = 0   # 最近一次 poll 返回成交条数 (用于判定是否触顶)
        self._last_cycle_trade_count    = 0   # 最近一次 drain 循环累计处理成交条数
        self._last_cycle_bar_count      = 0   # 最近一次 drain 循环产出 Bar 数
        self._last_cycle_wall_time_ms   = 0   # 最近一次 drain 循环挂钟耗时
        self._last_cycle_catchup_rounds = 0   # 最近一次 drain 循环 poll 次数
        self._last_cycle_hit_wall_time  = False  # 是否因挂钟超时退出
        self._last_cycle_hit_limit      = False  # 是否出现过"poll 返回满 limit"（疑似积压）

        # v1.3.3: 采集全局计数器 (供状态栏"aggTrades 采集"行消费)
        # _poll_call_count_total : 累计 poll 次数 (含 drain 内多轮)
        # _last_success_fetch_ms : 上次成功从 REST 得到非空响应的挂钟时刻
        #                          "成功"定义: HTTP 未抛异常 AND 返回了 list 结构
        #                          即使 list 为空也算成功 (说明服务器在响应)
        self._poll_call_count_total = 0
        self._last_success_fetch_ms = 0

    def poll(self):
        """
        拉取新成交数据并处理为等币量柱。
        返回本次 poll 中新完成的柱列表（可能为空）。

        REST CVD 增强:
          每次 poll 开始时假设数据连续（_poll_had_gap = False）。
          若 _parse_trades 检测到 trade_id 断层，标记 _cvd_gap_degraded = True。
          只有在后续一次完整无缺口的 poll 成功后才恢复为 False。

        v1.3.2 新增:
          _last_poll_returned_count 记录本次 REST 返回的成交条数，供
          poll_with_drain() 判断"本轮是否已追上市场队尾"：
            returned_count < agg_trades_limit  → 追上了，退出 drain
            returned_count == agg_trades_limit → 可能仍有积压，继续 drain

        v1.3.3 新增:
          _poll_call_count_total += 1 累计所有 poll 调用 (含 drain 内多轮)
          _last_success_fetch_ms 在成功获得 list 响应时更新 (即使为空列表)
        """
        # v1.3.3: 累计 poll 次数 —— 本调用无论成功失败都算一次 API 尝试
        self._poll_call_count_total += 1

        try:
            raw_trades = self._fetch_agg_trades()
        except Exception as error:
            log_warn("BarAssembler poll error: " + str(error))
            self._last_poll_returned_count = 0
            return []

        # v1.3.3: 只要成功拿到 list 响应就算"上游有响应"
        # (空列表也算, 说明服务器通，只是暂时没新成交)
        if isinstance(raw_trades, list):
            self._last_success_fetch_ms = now_ms()

        # v1.3.2: 记录本轮 REST 返回的原始成交条数（过滤前）
        self._last_poll_returned_count = len(raw_trades) if isinstance(raw_trades, list) else 0

        self._poll_had_gap = False
        newly_completed = []
        for trade in self._parse_trades(raw_trades):
            self._last_trade_price = trade["price"]
            newly_completed.extend(self._ingest_trade(trade))

        if self._poll_had_gap:
            if CONFIG.get("cvd_gap_degrade_enabled"):
                self._cvd_gap_degraded = True
                log_warn("BarAssembler: CVD degraded due to trade_id gap")
        else:
            # 无缺口的完整 poll，恢复 CVD 可信度
            if self._cvd_gap_degraded:
                self._cvd_gap_degraded = False
                log_info("BarAssembler: CVD recovered, no gap in this poll")

        return newly_completed

    def _fetch_agg_trades(self):
        params = {
            "symbol": CONFIG["binance_symbol"],
            "limit":  CONFIG["agg_trades_limit"],
        }
        if self._last_trade_id is not None:
            params["fromId"] = self._last_trade_id + 1

        url = CONFIG["binance_url"] + "?" + urlencode(params)
        ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        req = urllib.request.Request(
            url=url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            method="GET",
        )
        resp = opener.open(req, timeout=CONFIG["binance_http_timeout_sec"])
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not isinstance(data, list):
            raise RuntimeError("aggTrades response is not a list")
        return data

    def _parse_trades(self, raw_rows):
        """
        解析 aggTrades 原始行。
        发现 ID 断层时 Log WARN 并继续（纪律3：不重置历史）。
        同时标记 _poll_had_gap 供 CVD 降级判断使用。
        """
        parsed = []
        for item in raw_rows:
            if not isinstance(item, dict):
                continue

            trade_id       = safe_int(item.get("a"))
            trade_price    = safe_float(item.get("p"))
            trade_qty      = safe_float(item.get("q"))
            is_buyer_maker = item.get("m")

            if (trade_id is None
                    or trade_price is None or trade_price <= 0
                    or trade_qty is None or trade_qty <= 0
                    or not isinstance(is_buyer_maker, bool)):
                continue

            # 断层检测 — 仅警告，绝不重置（纪律3）
            # 同时设置 _poll_had_gap 标记
            if (self._last_trade_id is not None
                    and trade_id > self._last_trade_id + 1
                    and not parsed):
                gap_size = trade_id - self._last_trade_id - 1
                log_warn("BarAssembler: trade_id gap "
                         + str(self._last_trade_id) + " → " + str(trade_id)
                         + " (missed " + str(gap_size) + " trades)"
                         + ", continuing without reset")
                self._poll_had_gap = True

            # m=True: 买方是 maker → 卖方是 taker → 负 CVD
            # m=False: 买方是 taker → 正 CVD
            signed_qty = trade_qty if not is_buyer_maker else -trade_qty

            parsed.append({
                "id":         trade_id,
                "price":      trade_price,
                "qty":        trade_qty,
                "signed_qty": signed_qty,
            })

        return parsed

    def _ingest_trade(self, trade):
        """
        将单笔成交吸收进当前未完成柱。
        单笔大成交可能跨越多根柱，通过 while 循环处理。
        返回本笔成交触发完成的柱列表。
        """
        bar_threshold  = CONFIG["volume_bar_n"]
        remaining_qty  = trade["qty"]
        completed_bars = []

        while remaining_qty > 0:
            # 初始化新柱的 open 价格
            if self._current_open is None:
                self._current_open = trade["price"]
                self._current_high = trade["price"]
                self._current_low  = trade["price"]

            space_in_bar = bar_threshold - self._current_volume
            if space_in_bar <= 0:
                completed_bars.extend(self._complete_current_bar())
                continue

            take_qty   = min(remaining_qty, space_in_bar)
            fill_ratio = take_qty / trade["qty"]

            self._current_high  = max(self._current_high, trade["price"])
            self._current_low   = min(self._current_low,  trade["price"])
            self._current_close = trade["price"]
            self._current_volume += take_qty
            self._current_cvd    += trade["signed_qty"] * fill_ratio

            self._last_trade_id = trade["id"]
            remaining_qty -= take_qty

            if self._current_volume >= bar_threshold:
                completed_bars.extend(self._complete_current_bar())

        return completed_bars

    def _complete_current_bar(self):
        """封存当前柱，推入环形缓冲区，重置聚合状态。"""
        if self._current_open is None:
            return []

        self._bar_index += 1
        bar = {
            "open":         self._current_open,
            "high":         self._current_high,
            "low":          self._current_low,
            "close":        self._current_close,
            "total_volume": self._current_volume,
            "cvd_delta":    self._current_cvd,
            "bar_index":    self._bar_index,
        }
        self._completed_bars.append(bar)

        self._current_open   = None
        self._current_high   = None
        self._current_low    = None
        self._current_close  = None
        self._current_volume = 0.0
        self._current_cvd    = 0.0

        return [bar]

    def get_slow_std_usd(self):
        """
        最近 K×3 根完成柱的收盘价"去趋势"总体标准差，作为 σ_slow。

        v1.2 修正:
          从 std_dev_population 改为 detrended_std_population。
          原始 std 在单边趋势下会被方向性位移污染，导致 band_half 自吞噬
          (价格快速上涨 → std 暴涨 → 带宽膨胀 → 脱离被错误吸收)。
          去趋势后 σ_slow 只反映真实震荡幅度，带宽在趋势中保持稳定。

        纪律1: 这是等币量时间标准差，不是墙上时钟标准差。
        窗口不足时返回 None（触发 band_half 降级模式）。
        """
        slow_window = CONFIG["K"] * 3
        if len(self._completed_bars) < slow_window:
            return None
        close_prices = [bar["close"] for bar in list(self._completed_bars)[-slow_window:]]
        return detrended_std_population(close_prices)

    def bar_count(self):
        return self._bar_index

    def poll_with_drain(self):
        """
        v1.3.2 新增: 受控 catch-up drain。

        在一轮主循环内连续调用 poll()，直到以下任一条件满足退出:

          退出条件 A — drain 被禁用:
            drain_enabled=False 时只 poll 一次，退化为 v1.3.1 行为

          退出条件 B — 市场没进步（最弱态，正常退出）:
            poll() 返回 0 条新成交，说明队尾已空

          退出条件 C — 已追上队尾（最常见，正常退出）:
            poll() 返回 < agg_trades_limit 条成交，说明本批已包含所有积压

          退出条件 D — poll 次数硬上限（防无限循环）:
            drain_rounds >= max_drain_rounds

          退出条件 E — 挂钟超时（防挤占其他 Step 的 CPU 份额）:
            累计耗时 >= max_drain_wall_time_ms
            此时标记 _last_cycle_hit_wall_time=True，Display 层会告知观察者

        为什么要这样设计:
          REST 是 pull 模型，按 fromId 拉取。主循环若耗时远大于流速产生 1000 条
          的时间（约 60-100 秒正常流速），传统单次 poll 永远追不上市场，
          导致系统处理"越来越旧的成交"。catch-up drain 允许一次主循环内
          把积压吃完，代价是单轮主循环可能多花 max_drain_wall_time_ms 毫秒。

        与"主循环只做调度"纪律的关系:
          drain 住在 BarAssembler 内部，run() 仍然只调用一次方法。
          GEX 节奏由 AnchorContext.check_update() 的内部 60s 节流保护，
          drain 不干涉 GEX 的轮询周期。

        纪律 3 （trade_id 断层不清空历史）仍然生效:
          drain 循环内遇到 gap 由 poll() 本身处理，drain 不做任何额外重置。

        返回:
          本轮 drain 累计产出的 completed_bars 列表。
        """
        # 每轮 drain 开始重置度量
        self._last_cycle_trade_count    = 0
        self._last_cycle_bar_count      = 0
        self._last_cycle_wall_time_ms   = 0
        self._last_cycle_catchup_rounds = 0
        self._last_cycle_hit_wall_time  = False
        self._last_cycle_hit_limit      = False

        drain_enabled      = bool(CONFIG.get("drain_enabled", True))
        max_rounds         = int(CONFIG.get("max_drain_rounds", 1))
        max_wall_time_ms   = int(CONFIG.get("max_drain_wall_time_ms", 3000))
        agg_trades_limit   = int(CONFIG.get("agg_trades_limit", 1000))

        deadline_ms        = now_ms() + max_wall_time_ms
        all_completed_bars = []

        while True:
            round_start_ms = now_ms()
            new_bars = self.poll()
            round_end_ms   = now_ms()

            self._last_cycle_catchup_rounds += 1
            self._last_cycle_trade_count    += self._last_poll_returned_count
            self._last_cycle_bar_count      += len(new_bars)
            self._last_cycle_wall_time_ms   += (round_end_ms - round_start_ms)
            all_completed_bars.extend(new_bars)

            # 退出条件 A: drain 被禁用 → 只 poll 一次
            if not drain_enabled:
                break

            # 退出条件 B: 市场完全没进步
            if self._last_poll_returned_count == 0:
                break

            # 退出条件 C: 本轮未触顶，已追上队尾（最常见的优雅退出）
            if self._last_poll_returned_count < agg_trades_limit:
                break

            # 触顶了（返回了满额 limit 条），记录疑似积压信号
            self._last_cycle_hit_limit = True

            # 退出条件 D: 达到 poll 次数上限
            if self._last_cycle_catchup_rounds >= max_rounds:
                break

            # 退出条件 E: 挂钟超时
            if now_ms() >= deadline_ms:
                self._last_cycle_hit_wall_time = True
                break

        return all_completed_bars

    def is_backlogged(self):
        """
        v1.3.2: 是否仍存在未消化的 backlog。
        判定: 触顶（单次 poll 返回满 limit）且触发了挂钟超时。
        含义: drain 循环用完了预算但仍没追上，市场流速 > REST 补给速率。

        单独触顶不一定是 backlog —— 可能只是一次突发成交批，下一轮就追上。
        单独超时也不一定是 —— 可能只是网络慢。
        两者同时出现才认为 backlog 未消化。
        """
        return bool(self._last_cycle_hit_limit and self._last_cycle_hit_wall_time)

    def get_last_cycle_metrics(self):
        """
        v1.3.2: 返回最近一次 poll_with_drain() 的采集度量。
        供 run() 写入 raw_values 和 Display 层消费。

        字段:
          trade_count        本轮累计处理成交条数
          bar_count          本轮产出 Bar 数
          wall_time_ms       本轮挂钟耗时
          catchup_rounds     本轮 poll 次数（1 表示单次，>1 表示触发了 drain）
          hit_wall_time      是否因挂钟超时退出
          hit_limit          是否出现过"poll 返回满 limit"
          backlogged         是否判定 backlog 未消化
        """
        return {
            "trade_count":    self._last_cycle_trade_count,
            "bar_count":      self._last_cycle_bar_count,
            "wall_time_ms":   self._last_cycle_wall_time_ms,
            "catchup_rounds": self._last_cycle_catchup_rounds,
            "hit_wall_time":  self._last_cycle_hit_wall_time,
            "hit_limit":      self._last_cycle_hit_limit,
            "backlogged":     self.is_backlogged(),
        }

    def is_cvd_degraded(self):
        """
        REST CVD 增强:
        返回当前 CVD 是否因 trade_id 缺口处于降级状态。
        降级期间 ClassificationEvidence 应将 cvd_strength 强制归零。
        """
        return self._cvd_gap_degraded

    # ── v1.3.3: 构造进度 + 采集计数器 getter ────────────────────
    def get_current_bar_build_progress(self):
        """
        v1.3.3: 返回当前未完成柱的构造进度。

        返回 dict:
          current_volume: 当前未完成柱已累积的 BTC
          target_volume:  目标体积 (即 CONFIG["volume_bar_n"])
          ratio:          current / target ∈ [0, 1)
                          新柱刚开始聚合时 ratio=0；柱完成瞬间值会被重置

        用于状态栏显示"柱构造进度"，让观察者知道下一根柱还差多少成交。
        如果 current_volume 长期卡在低值不动，说明流速极低或 poll 失败。
        """
        target = CONFIG["volume_bar_n"]
        current = self._current_volume
        if target > 0:
            ratio = current / target
        else:
            ratio = 0.0
        return {
            "current_volume": current,
            "target_volume":  target,
            "ratio":          ratio,
        }

    def get_last_success_fetch_ms(self):
        """v1.3.3: 上次 REST 成功拿到 list 响应的挂钟时刻。0 表示从未成功。"""
        return self._last_success_fetch_ms

    def get_poll_call_count_total(self):
        """v1.3.3: 累计 poll 次数 (含 drain 内多轮)。"""
        return self._poll_call_count_total


# ================================================================
# SECTION 4: MODULE 3 — DeviationTracker
# ================================================================

class DeviationTracker:
    """
    计算归一化偏差，管理脱离确认状态机。

    normalized_deviation = (close - flip_point) / band_half
      无量纲，跨价格级别和波动率制度可比较。
      |deviation| < 1  → inside band (吸收带内)
      |deviation| ≥ 1  → outside band (潜在脱离)

    事件状态机:
      INSIDE           带内正常震荡
      CANDIDATE        |deviation| ≥ 1，但连续 outside 柱数 < outside_bar_confirm
      CONFIRMED        连续 outside 柱数 ≥ outside_bar_confirm（脱离确认）
      REENTRY_PENDING  价格已返回带内，等待 inside_bar_confirm 柱确认缺口闭合
                       (防止价格短暂回带后又出去的假闭合)

    重置机制:
      schedule_reset(): 由 SystemStateManager 在锚偏移时调用，
                        下一根柱的 update() 开始时生效（one-bar lag 已知且可接受）。
    """

    INSIDE          = "INSIDE"
    CANDIDATE       = "CANDIDATE"
    CONFIRMED       = "CONFIRMED"
    REENTRY_PENDING = "REENTRY_PENDING"

    def __init__(self):
        self._event_state       = self.INSIDE
        self._outside_bar_count = 0
        self._inside_bar_count  = 0
        self._pending_reset     = False
        # v1.2: 标记"本次脱离事件是否已经触发过 departure_confirmed 事件"，
        # 防止 REENTRY_PENDING → CONFIRMED 回跳时重复触发。
        self._departure_signaled = False

    def schedule_reset(self):
        """SystemStateManager 在锚偏移时调用，下一柱生效。"""
        self._pending_reset = True

    def update(self, bar, flip_point, band_half):
        """
        处理一根完成的等币量柱，返回 deviation_state dict。
        """
        if self._pending_reset:
            self._event_state        = self.INSIDE
            self._outside_bar_count  = 0
            self._inside_bar_count   = 0
            self._departure_signaled = False
            self._pending_reset      = False
            log_info("DeviationTracker: reset applied (anchor shift)")

        if flip_point is None or band_half is None or band_half <= 0:
            return self._build_state(None)

        normalized_deviation = (bar["close"] - flip_point) / band_half
        is_outside = abs(normalized_deviation) >= CONFIG["deviation_threshold"]

        # v1.2: departure_confirmed 事件标记 (一次性，本柱有效)
        departure_confirmed_event = False

        if is_outside:
            self._outside_bar_count += 1
            self._inside_bar_count   = 0

            if self._event_state == self.INSIDE:
                self._event_state = self.CANDIDATE

            elif self._event_state == self.CANDIDATE:
                if self._outside_bar_count >= CONFIG["outside_bar_confirm"]:
                    self._event_state = self.CONFIRMED
                    # v1.2: 仅在首次进入 CONFIRMED 时发事件
                    if not self._departure_signaled:
                        departure_confirmed_event = True
                        self._departure_signaled = True

            elif self._event_state == self.REENTRY_PENDING:
                # 回带失败，价格再次突破 → 恢复 CONFIRMED
                # v1.2: 不重复发 departure_confirmed_event，因为本次脱离事件已信号过
                self._event_state      = self.CONFIRMED
                self._inside_bar_count = 0

            # CONFIRMED 状态: outside_bar_count 继续累积供 OLS 使用

        else:
            # 价格在带内
            self._outside_bar_count = 0

            if self._event_state == self.CONFIRMED:
                self._inside_bar_count = 1
                self._event_state      = self.REENTRY_PENDING

            elif self._event_state == self.REENTRY_PENDING:
                self._inside_bar_count += 1
                if self._inside_bar_count >= CONFIG["inside_bar_confirm"]:
                    # 缺口回补确认事件
                    self._event_state        = self.INSIDE
                    self._inside_bar_count   = 0
                    # v1.2: 一轮脱离彻底结束，重置 signal 标记供下轮使用
                    self._departure_signaled = False
                    return self._build_state(
                        normalized_deviation,
                        gap_closure_event=True)

            elif self._event_state == self.CANDIDATE:
                # 未达到确认阈值就回带 → 静默重置
                self._event_state      = self.INSIDE
                self._inside_bar_count = 0

            else:
                self._inside_bar_count = 0

        return self._build_state(
            normalized_deviation,
            departure_confirmed_event=departure_confirmed_event)

    def _build_state(self, normalized_deviation,
                     gap_closure_event=False,
                     departure_confirmed_event=False):
        return {
            "event_state":               self._event_state,
            "normalized_deviation":      normalized_deviation,
            "outside_bar_count":         self._outside_bar_count,
            "inside_bar_count":          self._inside_bar_count,
            "deviation_confirmed":       self._event_state == self.CONFIRMED,
            "gap_closure_event":         gap_closure_event,
            "departure_confirmed_event": departure_confirmed_event,
        }
# ================================================================
# SECTION 5: MODULE 4 — ClassificationEvidence
# ================================================================

class ClassificationEvidence:
    """
    计算分类层所需的全部原始因子值。
    所有阈值和标签化逻辑在 LabelGenerator 中，这里只输出原始数值。

    因子:
      PPE (Price Path Efficiency) = |close - open| / (high - low)
        ≈ 1: 单向运动，MM 吸收力弱
        ≈ 0: 来回震荡，MM 正在主动吸收

      PPE_percentile: 当前 PPE 在 20K 柱历史中的分位排名
        百分位基于大样本历史，非常平滑，不存在单柱抖动问题。

      PPE_short_median: 最近 K 根带内柱的 PPE 中位数
        带外时冻结，不纳入新数据（锚状态描述不应受带外噪声污染）。

      OLS slope + R²: 对 outside 柱归一化偏差序列的线性回归
        纪律1: 斜率单位是"σ/等币量柱"，不是"σ/秒"。
        仅对 outside 柱计算，带内柱不参与（防止信号污染）。

      CVD direction + strength: K 柱累积买卖方向压力
        strength gate 防止将弱流向误标为方向性信号。

    T-1 readiness cache: 供下一柱的 SystemStateManager 读取，
    消除 SSM(Step3) 和 CE(Step4) 之间的循环依赖。
    """

    def __init__(self):
        ppe_hist_window  = CONFIG["ppe_history_window"]
        ols_win          = CONFIG["ols_window"]
        cvd_win          = CONFIG["cvd_window"]
        ppe_short_win    = CONFIG["ppe_short_window"]

        self._ppe_history        = collections.deque(maxlen=ppe_hist_window)
        self._ppe_short_buffer   = collections.deque(maxlen=ppe_short_win)
        self._outside_deviations = collections.deque(maxlen=ols_win)
        self._cvd_buffer         = collections.deque(maxlen=cvd_win)
        self._absorption_frozen  = False

        # v1.3: 中心性缓冲区 (带内结构健康观测)
        # 存储 d_capped = clamp(normalized_deviation, ±d_cap)，
        # 窗口与 OLS 对齐 (3K)。CONFIRMED 期间冻结不写入。
        self._centrality_buffer = collections.deque(
            maxlen=CONFIG["centrality_window"])

        # v1.3: 冻结期间复用的最近一次有效原始值。
        # CONFIRMED 进入后，本次 compute() 不再重算，直接透出这里的快照。
        # reset_ppe_history 触发时同步清空（失去参照系）。
        self._last_centrality_values = {
            "ed1_raw":           None,
            "sign_consistency":  None,
            "center_loss":       None,
            "erosion_drift":     None,
        }

        # T-1 缓存: 当前柱计算完后更新，下一柱 SSM 读取
        self._readiness_cache = {
            "ppe_history_ready":   False,
            "ols_window_ready":    False,
            "r_squared_available": False,
            "deviation_confirmed": False,
        }

    def compute(self, bar, flip_point, band_half, dev_state, instructions,
                cvd_degraded=False):
        """
        计算一根完成柱的所有证据因子。
        instructions: SystemStateManager 在 Step3 发出的指令 dict。
        cvd_degraded: REST 增强 — True 时 CVD 强度强制归零。
        """
        # ── 执行 SSM 指令 ────────────────────────────────────────
        if instructions.get("reset_ols_window"):
            self._outside_deviations.clear()
            # v1.3 纪律: reset_ols_window 不清空 _centrality_buffer。
            # 中心性以"当前锚位置"为参照，与 OLS 的"本轮脱离序列"生命周期解耦。

        if instructions.get("reset_ppe_history"):
            self._ppe_history.clear()
            self._ppe_short_buffer.clear()
            # v1.3: 锚大位移 → 中心性失去参照系，同步清空
            self._centrality_buffer.clear()
            for key in self._last_centrality_values:
                self._last_centrality_values[key] = None

        # (规范 7.4): 价格回到带内时重置吸收趋势短窗口
        # "从当前 Bar 开始重新积累带内序列，窗口重置"
        if instructions.get("reset_ppe_short_buffer"):
            self._ppe_short_buffer.clear()

        self._absorption_frozen = instructions.get("freeze_absorption_trend", False)

        event_state          = dev_state.get("event_state", DeviationTracker.INSIDE)
        normalized_deviation = dev_state.get("normalized_deviation")

        # ── PPE ──────────────────────────────────────────────────
        # (规范 7.3):
        #   尖峰 PPE "不参与 PPE 历史分布更新，但保留原始值记录"。
        #   _compute_ppe 返回 (ppe_raw, is_spike) 二元组。
        ppe_raw, ppe_is_spike = self._compute_ppe(bar, band_half)

        # v1.3.1 Fix-3: 先算百分位再 append，消除自包含偏差。
        # 旧实现先 append 后算 percentile_rank，会把当前样本计入自己的历史分布，
        # 窗口满时偏差 ≈ 1/400 可忽略，但冷启动期（窗口很小）偏差显著，
        # 且始终让当前值至少占 1/n 的"自吹自擂"。现在严格用"历史"做参照系。
        ppe_history_snapshot = list(self._ppe_history)
        ppe_percentile       = percentile_rank(ppe_raw, ppe_history_snapshot)

        if ppe_raw is not None and not ppe_is_spike:
            # 正常 PPE: 同时进入历史分布和短窗口（百分位已在上面算完）
            self._ppe_history.append(ppe_raw)
            if event_state == DeviationTracker.INSIDE and not self._absorption_frozen:
                self._ppe_short_buffer.append(ppe_raw)
        # 尖峰 PPE: ppe_raw 保留原始值供快照记录，但不进入任何历史窗口

        ppe_short_median = median_of(list(self._ppe_short_buffer))

        # ── OLS: 仅累积确认脱离后的带外柱偏差 ─────────────────────
        # (规范 7.7): 仅 CONFIRMED 状态的带外柱参与 OLS。
        # CANDIDATE: 未确认脱离的第一根 outside，不参与。
        # REENTRY_PENDING: 价格已回带内，|deviation| < 1，不应混入带外序列。
        is_confirmed_outside = (event_state == DeviationTracker.CONFIRMED)
        if is_confirmed_outside and normalized_deviation is not None:
            self._outside_deviations.append(normalized_deviation)

        ols_slope_val = None
        r_squared     = None
        if len(self._outside_deviations) >= CONFIG["ols_min_bars"]:
            ols_slope_val, r_squared = ols_slope_and_r2(list(self._outside_deviations))

        # ── CVD ──────────────────────────────────────────────────
        cvd_delta = bar.get("cvd_delta")
        if cvd_delta is not None:
            self._cvd_buffer.append(cvd_delta)

        cvd_sum        = sum(self._cvd_buffer) if self._cvd_buffer else 0.0
        cvd_buf_len    = len(self._cvd_buffer)
        volume_bar_n   = CONFIG["volume_bar_n"]

        if cvd_buf_len > 0:
            # 归一化: |cvd_sum| / (K × volume_bar_n)
            # 分母 = "若所有柱均为单向成交时的最大绝对 CVD"
            cvd_strength = abs(cvd_sum) / (cvd_buf_len * volume_bar_n)
        else:
            cvd_strength = 0.0

        if cvd_sum > 0:
            cvd_direction = 1
        elif cvd_sum < 0:
            cvd_direction = -1
        else:
            cvd_direction = 0

        # REST CVD 增强: trade_id 缺口时强制降级
        # CVD 缓冲区内数据不完整，强度归零，方向保留但标签层会输出 neutral。
        if cvd_degraded:
            cvd_strength = 0.0

        # ── 吸收趋势: 对带内 PPE 短窗口做 OLS ────────────────────
        absorption_trend_slope = None
        if len(self._ppe_short_buffer) >= 3:
            absorption_trend_slope, _ = ols_slope_and_r2(list(self._ppe_short_buffer))

        # ── v1.3: 中心性原始因子计算 ──────────────────────────────
        # 纪律:
        #   - INSIDE / CANDIDATE / REENTRY_PENDING 期间更新 _centrality_buffer
        #     并重算中心性原始值
        #   - CONFIRMED 期间冻结，不写入新数据，透出最近一次有效值
        #   - normalized_deviation is None 时跳过 (与 OLS 处理一致)
        #   - 最少柱数不足时输出 None
        centrality_frozen = (event_state == DeviationTracker.CONFIRMED)

        if not centrality_frozen and normalized_deviation is not None:
            d_capped = max(
                -CONFIG["centrality_d_cap"],
                min(CONFIG["centrality_d_cap"], normalized_deviation))
            self._centrality_buffer.append(d_capped)

            if len(self._centrality_buffer) >= CONFIG["centrality_min_bars"]:
                ed1_val, sign_cons_val, center_loss_val = (
                    self._compute_centrality_factors())
                erosion_drift_val = (
                    abs(ed1_val) * sign_cons_val
                    if (ed1_val is not None and sign_cons_val is not None)
                    else None)
                self._last_centrality_values = {
                    "ed1_raw":           ed1_val,
                    "sign_consistency":  sign_cons_val,
                    "center_loss":       center_loss_val,
                    "erosion_drift":     erosion_drift_val,
                }
            # else: 冷启动或刚清空，保留上次值 (可能为 None)

        # 冻结期或未更新时透出最近一次有效快照
        ed1_raw          = self._last_centrality_values["ed1_raw"]
        sign_consistency = self._last_centrality_values["sign_consistency"]
        center_loss      = self._last_centrality_values["center_loss"]
        erosion_drift    = self._last_centrality_values["erosion_drift"]
        centrality_buffer_len = len(self._centrality_buffer)

        # ── 更新 T-1 readiness cache ──────────────────────────────
        ols_window_ready = len(self._outside_deviations) >= CONFIG["ols_min_bars"]
        r_squared_avail  = (r_squared is not None
                            and r_squared >= CONFIG["ols_r2_min"])
        self._readiness_cache = {
            "ppe_history_ready":   len(self._ppe_history) >= CONFIG["ppe_history_window"],
            "ols_window_ready":    ols_window_ready,
            "r_squared_available": r_squared_avail,
            "deviation_confirmed": dev_state.get("deviation_confirmed", False),
        }

        return {
            "ppe_raw":               ppe_raw,
            "ppe_is_spike":          ppe_is_spike,
            "ppe_percentile":        ppe_percentile,
            "ppe_short_median":      ppe_short_median,
            "ols_slope":             ols_slope_val,
            "r_squared":             r_squared,
            "cvd_direction":         cvd_direction,
            "cvd_strength":          cvd_strength,
            "cvd_degraded":          cvd_degraded,
            "absorption_trend_slope": absorption_trend_slope,
            "absorption_frozen":     self._absorption_frozen,
            # v1.3 中心性原始因子
            "ed1_raw":               ed1_raw,
            "sign_consistency":      sign_consistency,
            "center_loss":           center_loss,
            "erosion_drift":         erosion_drift,
            "centrality_frozen":     centrality_frozen,
            "centrality_buffer_len": centrality_buffer_len,
        }

    def _compute_centrality_factors(self):
        """
        v1.3: 计算三个正交的中心性原始因子。
        调用前置条件: len(_centrality_buffer) >= centrality_min_bars。

        指数权重:
          w_i = exp(-ln(2) × age_i / halflife)
          age_i 按"从当前 Bar 回数"计算 (最新 Bar age=0)

        三个因子:
          ED1 = Σ(w_i × d_i) / Σ(w_i)                  [含符号, 值域 ±d_cap]
          sign_consistency = |Σ(w_i × sign_eps(d_i))| / Σ(w_i)   [0, 1]
          center_loss = sqrt(Σ(w_i × d_i²) / Σ(w_i))   [0, d_cap]

        其中 sign_eps(d) 有 deadzone:
          |d| < sign_eps → 0
          d > 0          → +1
          d < 0          → -1

        返回 (ED1, sign_consistency, center_loss)
        任一因子计算失败返回 (None, None, None)。
        """
        buffer_snapshot = list(self._centrality_buffer)
        n = len(buffer_snapshot)
        if n < 1:
            return None, None, None

        halflife = CONFIG["centrality_ewma_halflife"]
        sign_eps = CONFIG["centrality_sign_eps"]
        decay_constant = math.log(2.0) / float(halflife)

        sum_weight          = 0.0
        sum_weighted_d      = 0.0
        sum_weighted_sign   = 0.0
        sum_weighted_d_sq   = 0.0

        for idx, d_val in enumerate(buffer_snapshot):
            age = n - 1 - idx  # 最新 Bar age=0, 最老 Bar age=n-1
            weight = math.exp(-decay_constant * age)
            sum_weight          += weight
            sum_weighted_d      += weight * d_val
            sum_weighted_d_sq   += weight * d_val * d_val
            if d_val > sign_eps:
                sum_weighted_sign += weight
            elif d_val < -sign_eps:
                sum_weighted_sign -= weight
            # |d_val| < sign_eps 的 Bar 贡献 0，落入 deadzone

        if sum_weight <= 0.0:
            return None, None, None

        ed1              = sum_weighted_d / sum_weight
        sign_consistency = abs(sum_weighted_sign) / sum_weight
        variance_weighted = sum_weighted_d_sq / sum_weight
        # 数值保护: 浮点误差可能使其略小于 0
        center_loss      = math.sqrt(max(0.0, variance_weighted))

        return ed1, sign_consistency, center_loss

    def _compute_ppe(self, bar, band_half):
        """
        PPE = |close - open| / (high - low)

        (规范 7.3):
          尖峰 bar (振幅 > ppe_spike_mult × band_half) 的 PPE 原始值
          仍然计算并保留供快照记录，但标记 is_spike=True，
          调用方据此跳过历史分布更新。

        返回 (ppe_raw, is_spike) 二元组。
          bar_range <= 0 时返回 (None, False)。
        """
        bar_range = bar["high"] - bar["low"]
        if bar_range <= 0:
            return None, False

        ppe_raw = abs(bar["close"] - bar["open"]) / bar_range

        is_spike = False
        if band_half is not None and band_half > 0:
            if bar_range > CONFIG["ppe_spike_mult"] * band_half:
                is_spike = True

        return ppe_raw, is_spike

    def get_readiness_cache(self):
        """返回 T-1 readiness 信号副本供 SystemStateManager 读取。"""
        return dict(self._readiness_cache)


# ================================================================
# SECTION 6: MODULE 5 — SystemStateManager
# ================================================================

class SystemStateManager:
    """
    四轴状态机。唯一可以改变全局状态的模块。

    四轴:
      runtime_gate         COLD_START → READY（PPE 历史填满后）
      anchor_state         FRESH / STALE / EXPIRED / RESETTING
      event_state          镜像 DeviationTracker 输出
      classification_state UNAVAILABLE / PARTIAL / AVAILABLE

    T-1 缓存策略（消除 SSM-CE 循环依赖）:
      CE 在 Step4 末尾更新 readiness cache，
      SSM 在下一柱 Step3 读取该 cache 做决策。
      这样 SSM 发指令时不依赖 CE 的当前输出，CE 执行指令时不依赖 SSM 的状态。

    发出的指令:
      reset_ols_window        清空 OLS outside 偏差缓冲区（锚偏移时）
      freeze_absorption_trend 停止更新带内 PPE 短窗口（价格在带外时）
      reset_deviation_counter 通知 DeviationTracker 下柱重置（锚偏移时）
      reset_ppe_history       保留，仅在极端异常时使用
    """

    def __init__(self):
        self._runtime_gate            = "COLD_START"
        self._anchor_state            = AnchorContext.FRESH
        self._event_state             = DeviationTracker.INSIDE
        self._classification_state    = "UNAVAILABLE"
        self._anchor_stable_bar_count = 0

    def update(self, anchor_freshness, anchor_shift_event, shift_magnitude,
               dev_state, prev_readiness):
        """
        Step3 每柱调用一次。
        prev_readiness: T-1 缓存，来自上一柱 CE.get_readiness_cache()。
        shift_magnitude: 锚位移相对量（|位移| / band_half），仅在 anchor_shift_event=True 时有值。
        """
        instructions = {
            "reset_ols_window":        False,
            "freeze_absorption_trend": False,
            "reset_deviation_counter": False,
            "reset_ppe_history":       False,
            "reset_ppe_short_buffer":  False,
        }

        # ── 锚状态轴 ─────────────────────────────────────────────
        if anchor_shift_event:
            self._anchor_state            = "RESETTING"
            self._anchor_stable_bar_count = 0
            instructions["reset_ols_window"]        = True
            instructions["reset_deviation_counter"] = True
            # v1.3.1 Fix-6: 分层阈值响应。
            #   anchor_shift_frac     触发 RESETTING + OLS + deviation 重置（本分支已完成）
            #   anchor_ppe_reset_frac 额外触发 PPE 历史清空（更大位移才执行）
            # 旧实现把两档绑在同一阈值 anchor_shift_frac 上，使分层坍缩为一档，
            # 任何 shift_event 都会清空 20K 根 PPE 历史（5-6 小时数据），
            # 现在拆开后默认 ppe_reset_frac=1.0 > shift_frac=0.5，仅在更大位移时清空。
            ppe_reset_frac = CONFIG["anchor_ppe_reset_frac"]
            if shift_magnitude is not None and shift_magnitude > ppe_reset_frac:
                instructions["reset_ppe_history"] = True
                log_info("SystemStateManager: anchor shift → RESETTING, "
                         "OLS + PPE history reset (magnitude={:.2f} > ppe_reset_frac={:.2f})".format(
                             shift_magnitude, ppe_reset_frac))
            else:
                log_info("SystemStateManager: anchor shift → RESETTING, "
                         "OLS reset only (magnitude={:.2f}, below ppe_reset_frac={:.2f})".format(
                             shift_magnitude if shift_magnitude is not None else float("nan"),
                             ppe_reset_frac))

        elif self._anchor_state == "RESETTING":
            self._anchor_stable_bar_count += 1
            if self._anchor_stable_bar_count >= CONFIG["anchor_stable_bars"]:
                self._anchor_state = anchor_freshness
                log_info("SystemStateManager: anchor stable → " + anchor_freshness)
        else:
            self._anchor_state = anchor_freshness

        # ── 运行门轴 ─────────────────────────────────────────────
        if self._runtime_gate == "COLD_START":
            if prev_readiness.get("ppe_history_ready"):
                self._runtime_gate = "READY"
                log_info("SystemStateManager: PPE history ready → READY")

        # ── 脱离状态轴 ───────────────────────────────────────────
        prev_event_state = self._event_state
        self._event_state = dev_state.get("event_state", DeviationTracker.INSIDE)

        # (规范 7.7): 缺口回补确认后清空 OLS 窗口
        # gap_closure_event 标志着一轮脱离的结束，旧的带外偏差序列
        # 不应残留到下一轮脱离。
        if dev_state.get("gap_closure_event"):
            instructions["reset_ols_window"] = True
            log_info("SystemStateManager: gap closure → OLS window reset")

        # (规范 7.4): 价格回到 INSIDE 时重置吸收趋势短窗口
        # "价格重新回到带内时：标签解冻，从当前 Bar 开始重新积累带内序列，
        # 窗口重置。" 包括 gap_closure 和 CANDIDATE 回带两种路径。
        if (self._event_state == DeviationTracker.INSIDE
                and prev_event_state != DeviationTracker.INSIDE):
            instructions["reset_ppe_short_buffer"] = True

        # ── 冻结吸收趋势（价格在带外时）─────────────────────────
        if self._event_state in (DeviationTracker.CANDIDATE,
                                  DeviationTracker.CONFIRMED,
                                  DeviationTracker.REENTRY_PENDING):
            instructions["freeze_absorption_trend"] = True

        # ── 分类可用性轴 ─────────────────────────────────────────
        self._classification_state = self._compute_classification_state(prev_readiness)

        return instructions

    def _compute_classification_state(self, prev_readiness):
        """
        判断分类层能输出什么级别的结果 (规范 8.7 节):

          UNAVAILABLE:
            runtime_gate = COLD_START
            或 anchor_state IN [EXPIRED, RESETTING]
            或 event_state ≠ CONFIRMED / REENTRY_PENDING
            或 ppe_history_ready = False

          PARTIAL:
            event_state = CONFIRMED / REENTRY_PENDING
            且 anchor_state IN [FRESH, STALE]
            且 ppe_history_ready = True
            且 (ols_window_ready = False 或 r_squared_available = False)
            注: anchor_state = STALE 也只能到 PARTIAL，不能到 AVAILABLE

          AVAILABLE:
            event_state = CONFIRMED / REENTRY_PENDING
            且 anchor_state = FRESH               (规范要求 FRESH)
            且 ppe_history_ready = True
            且 ols_window_ready = True             (3K 根带外柱)
            且 r_squared_available = True           (规范要求 R² 检查)
        """
        if self._runtime_gate == "COLD_START":
            return "UNAVAILABLE"

        if self._anchor_state in ("EXPIRED", "RESETTING"):
            return "UNAVAILABLE"

        if self._event_state not in (DeviationTracker.CONFIRMED,
                                      DeviationTracker.REENTRY_PENDING):
            return "UNAVAILABLE"

        if not prev_readiness.get("ppe_history_ready"):
            return "UNAVAILABLE"

        # 三个条件全部满足才可 AVAILABLE
        ols_ready = prev_readiness.get("ols_window_ready", False)
        r2_ready  = prev_readiness.get("r_squared_available", False)

        if self._anchor_state != "FRESH":
            # STALE 状态: 有证据但坐标系不完全可信，最多 PARTIAL
            return "PARTIAL"

        if not ols_ready or not r2_ready:
            return "PARTIAL"

        return "AVAILABLE"

    def get_state(self):
        return {
            "runtime_gate":         self._runtime_gate,
            "anchor_state":         self._anchor_state,
            "event_state":          self._event_state,
            "classification_state": self._classification_state,
        }

    def get_anchor_stable_bar_count(self):
        """
        v1.3: 暴露 RESETTING 期间的稳定柱计数，供 LabelGenerator 计算
        H_stability 使用。不改变状态机行为，仅加 getter。
        其他状态下该值可能为 0（已重置）或历史值（不影响健康度，
        因为 H_stability 在非 RESETTING 时硬取 1.0）。
        """
        return self._anchor_stable_bar_count


# ================================================================
# SECTION 7: MODULE 6 — LabelGenerator
# ================================================================

class LabelGenerator:
    """
    将原始因子值映射为人类可读标签。
    所有校准阈值集中在此类——修改阈值只改这一处。

    分类矩阵主轴: OLS 斜率标签 × CVD 方向标签
    空间维度 (PPE): 对 HIGH 置信度迁移结论有否决权。
    """

    # 分类矩阵: (ols_label, cvd_label) → (分类名称, 置信度)
    CLASSIFICATION_MATRIX = {
        ("expansion",   "same"):     ("定价中心迁移", "HIGH"),
        ("expansion",   "neutral"):  ("迁移候选",     "LOW"),
        ("expansion",   "opposite"): ("迁移候选",     "LOW"),
        ("contraction", "same"):     ("暂时性缺口",   "MEDIUM"),
        ("contraction", "neutral"):  ("暂时性缺口",   "LOW"),
        ("contraction", "opposite"): ("可恢复缺口",   "HIGH"),
        ("oscillation", "same"):     ("不明确",        "LOW"),
        ("oscillation", "neutral"):  ("不明确",        "LOW"),
        ("oscillation", "opposite"): ("倾向恢复",      "LOW"),
        ("invalid",     "any"):      ("部分证据",      "PARTIAL"),
    }

    def generate(self, raw_evidence, dev_state, system_state,
                 anchor_source_ts_ms=None, anchor_stable_bar_count=None):
        """
        从原始证据和状态生成全部标签，返回 labels dict。

        v1.3 新增参数:
          anchor_source_ts_ms: int | None
            GEX 数据源时间戳，用于计算 H_time。由主循环从 AnchorContext
            取值后以值传入，不传对象引用。
          anchor_stable_bar_count: int | None
            RESETTING 期间累计的稳定柱数，由主循环从 SSM 取值后传入。
            仅在 anchor_state == "RESETTING" 时影响 H_stability。

        v1.3 新增标签字段:
          erosion_side, anchor_health_score, anchor_health_level
        """
        normalized_deviation  = dev_state.get("normalized_deviation")
        classification_state  = system_state.get("classification_state", "UNAVAILABLE")

        ols_label            = self._make_ols_label(raw_evidence)
        cvd_label            = self._make_cvd_label(raw_evidence, normalized_deviation)
        ppe_quality          = self._make_ppe_quality_label(raw_evidence)
        absorption_trend_tag = self._make_absorption_trend_tag(raw_evidence)
        anchor_validity      = self._make_anchor_validity_label(system_state)

        classification_result = None
        confidence            = None

        if classification_state == "AVAILABLE":
            classification_result, confidence = self._classify(
                ols_label, cvd_label, ppe_quality)
        elif classification_state == "PARTIAL":
            classification_result = "部分证据"
            confidence            = "PARTIAL"

        # v1.3: 侵蚀方向标签
        erosion_side = self._make_erosion_side_label(raw_evidence)

        # v1.3: 锚健康度聚合 (乘法式因子链)
        anchor_health_score, anchor_health_level, anchor_health_breakdown = (
            self._compute_anchor_health(
                raw_evidence, system_state,
                anchor_source_ts_ms, anchor_stable_bar_count))

        return {
            "ols_label":             ols_label,
            "cvd_label":             cvd_label,
            "ppe_quality":           ppe_quality,
            "absorption_trend_tag":  absorption_trend_tag,
            "anchor_validity":       anchor_validity,
            "classification_result": classification_result,
            "confidence":            confidence,
            # v1.3
            "erosion_side":          erosion_side,
            "anchor_health_score":   anchor_health_score,
            "anchor_health_level":   anchor_health_level,
            # v1.3 健康度因子分解 (UI 用途, 不参与任何决策)
            "anchor_health_breakdown": anchor_health_breakdown,
        }

    def _make_ols_label(self, raw_evidence):
        ols_slope_val = raw_evidence.get("ols_slope")
        r_squared     = raw_evidence.get("r_squared")

        if ols_slope_val is None or r_squared is None:
            return "invalid"
        if r_squared < CONFIG["ols_r2_min"]:
            return "invalid"
        if ols_slope_val > CONFIG["ols_exp_thresh"]:
            return "expansion"
        if ols_slope_val < CONFIG["ols_con_thresh"]:
            return "contraction"
        return "oscillation"

    def _make_cvd_label(self, raw_evidence, normalized_deviation):
        cvd_strength  = raw_evidence.get("cvd_strength", 0.0) or 0.0
        cvd_direction = raw_evidence.get("cvd_direction", 0) or 0

        if cvd_strength < CONFIG["cvd_strength_gate"]:
            return "neutral"

        if normalized_deviation is None:
            return "neutral"

        # v1.3.1 Fix-5: normalized_deviation 为 0 或极小时无方向可言，直接中性。
        # 旧实现 "deviation_sign = 1 if nd > 0 else -1" 在 nd==0 时会错误落到 -1 方向，
        # 产生无意义的 "same/opposite" 标签。严格性考虑，采用 sign_eps 作为 deadzone。
        # 阈值复用 centrality_sign_eps（0.10）保持语义一致：同一套 deadzone 判断
        # "是否有明确方向"。
        if abs(normalized_deviation) < CONFIG["centrality_sign_eps"]:
            return "neutral"

        # "same" = CVD 方向与偏离方向一致（买盘支撑上方脱离，或卖盘支撑下方脱离）
        deviation_sign = 1 if normalized_deviation > 0 else -1
        if cvd_direction == deviation_sign:
            return "same"
        return "opposite"

    def _make_ppe_quality_label(self, raw_evidence):
        """
        (规范 8.8 节):
        分类层 ppe_quality 基于 ppe_raw 做阈值映射，不是 ppe_percentile。
        ppe_raw 回答"这根 Bar 有没有吸收"（绝对判断），
        ppe_percentile 回答"和历史比算不算吸收"（相对判断，用于锚判定层）。
        """
        ppe_raw = raw_evidence.get("ppe_raw")
        if ppe_raw is None:
            return "unknown"
        if ppe_raw < CONFIG["ppe_quality_high_resistance"]:
            return "high_resistance"
        if ppe_raw > CONFIG["ppe_quality_low_resistance"]:
            return "low_resistance"
        return "neutral"

    def _make_absorption_trend_tag(self, raw_evidence):
        """
        (规范 7.4 节):
        基于带内 PPE 短窗口的 OLS 斜率判断吸收趋势。

        PPE 上升 (slope > 0) → 路径效率升高 → 吸收减弱 → "锚承压中"
        PPE 下降 (slope < 0) → 路径效率降低 → 吸收增强 → "锚修复中"

        (规范 8.8 节):
        freeze_absorption_trend = True 时，直接输出"冻结中"，
        不再基于旧斜率输出承压/修复/稳定（避免观察者误读为实时判断）。
        """
        # 规范 8.8: 冻结优先于任何斜率判断
        if raw_evidence.get("absorption_frozen"):
            return "冻结中"

        slope = raw_evidence.get("absorption_trend_slope")
        if slope is None:
            return "锚状态未知"
        if slope > CONFIG["absorption_trend_stress_slope"]:
            return "锚承压中"
        if slope < CONFIG["absorption_trend_recover_slope"]:
            return "锚修复中"
        return "锚状态稳定"

    def _make_anchor_validity_label(self, system_state):
        anchor_state = system_state.get("anchor_state", "EXPIRED")
        label_map = {
            "FRESH":     "有效",
            "STALE":     "轻微延迟",
            "EXPIRED":   "已过期",
            "RESETTING": "重置中",
        }
        return label_map.get(anchor_state, "未知")

    def _classify(self, ols_label, cvd_label, ppe_quality):
        """
        查分类矩阵，PPE 对 HIGH 置信度迁移结论有否决权:
        若 PPE 显示强吸收 (high_resistance)，即使 OLS/CVD 指向迁移，
        也意味着做市商在主动对抗——将置信度从 HIGH 降为 MEDIUM。
        """
        if ols_label == "invalid":
            return self.CLASSIFICATION_MATRIX[("invalid", "any")]

        key = (ols_label, cvd_label)
        if key not in self.CLASSIFICATION_MATRIX:
            key = (ols_label, "neutral")

        classification, confidence = self.CLASSIFICATION_MATRIX.get(
            key, ("不明确", "LOW"))

        if (confidence == "HIGH"
                and classification == "定价中心迁移"
                and ppe_quality == "high_resistance"):
            confidence = "MEDIUM"

        return classification, confidence

    # ── v1.3: 侵蚀方向 + 锚健康度 ────────────────────────────────

    def _make_erosion_side_label(self, raw_evidence):
        """
        v1.3: 根据 ED1 方向和绝对值阈值生成侵蚀方向标签。

        规则:
          ED1 > +threshold  → "上侵蚀"
          ED1 < -threshold  → "下侵蚀"
          |ED1| ≤ threshold → "中性"
          ED1 is None       → "中性" (冷启动期默认中性)

        阈值: CONFIG["erosion_side_threshold"] (默认 0.30)
        """
        ed1 = raw_evidence.get("ed1_raw")
        if ed1 is None:
            return "中性"
        threshold = CONFIG["erosion_side_threshold"]
        if ed1 > threshold:
            return "上侵蚀"
        if ed1 < -threshold:
            return "下侵蚀"
        return "中性"

    def _compute_anchor_health(self, raw_evidence, system_state,
                                anchor_source_ts_ms, anchor_stable_bar_count):
        """
        v1.3: 乘法式锚健康度评分 [0, 100]。

        总分公式:
          score = 100 × H_time × H_space × H_micro × H_stability

        其中 H_space 采用几何平均而非直接乘积:
          H_space = sqrt(H_erosion × H_center_loss)

        几何平均的目的 (修正 B): v1.3 第一版是观测版，不是风险门控版，
        直接乘积会让空间域双重扣分导致分数长期过低不可读。几何平均
        保持"两维度都差才严重扣分"的语义，但单维度差时扣分更温和。

        缺值策略 (规范 §13.3):
          anchor_source_ts_ms is None → H_time = 0.5 (中性)
          erosion_drift is None       → H_erosion = 0.5 (中性)
          center_loss is None         → H_center_loss = 0.5 (中性)
          ppe_percentile is None      → H_micro = 0.5 (中性)

        v1.3.4 readiness 语义收口 (H-1):
          旧版 (v1.3 ~ v1.3.3) 把 runtime_gate == COLD_START 作为
          health=None 的一票否决条件。而 runtime_gate 转 READY 需要
          ppe_history 填满 20K = 400 根柱——这是"分类层就绪"的前置,
          不是"锚层就绪"的前置。实盘出现 3 小时 300 柱 / GEX FRESH /
          中心性因子都有值的场景下 health 仍显示未就绪的 bug 就出在这里。

          v1.3.4 修正: health=None 的合法触发只剩两种:
            (a) anchor_source_ts_ms 为 None 或 0 — GEX 从未成功获取过
                → 没有锚位置, 任何基于 flip_point 的度量都无物理意义
            (b) anchor_state == EXPIRED — 锚参照系已失效
                → 延续 v1.3.1 Fix-2 的判断 (伪精度保护)

          其他场景 (包括 runtime_gate == COLD_START 但 GEX 已就位)
          都按各因子缺值走 0.5 中性的既定策略计算, 不再一票否决。

          "锚层自身就绪" vs "分类层就绪" 的分离:
            - anchor_health_score 是锚层观测, 只要锚在就可算
            - classification_state 是分类层观测, 需要 OLS/PPE 历史满窗
            两者 readiness 条件天然不同, v1.3.4 把它们解耦。

        纪律: 本方法输出不影响 classification_state / event_state，
              不触发事件，不新增图表元素。仅用于观测和复盘。

        返回 (score: float | None, level: str, breakdown: dict | None)
        breakdown 字段: {h_time, h_erosion, h_center_loss, h_space, h_micro,
                         h_stability}。仅 UI 展示使用，不参与任何决策。
        """
        anchor_state = system_state.get("anchor_state", "EXPIRED")

        # v1.3.4 H-1: readiness 早退 (a) —— GEX 从未成功获取过
        # safe_int 把 None / 0 统一判断为"未成功"。非 0 即视为已成功过一次。
        # 这一条保护了 "系统刚启动 1 秒, GEX 还没 fetch 到" 的边缘情况不产生
        # 基于中性 H_time=0.5 的虚假分数。
        if not anchor_source_ts_ms:
            return None, "未就绪", None

        # v1.3.1 Fix-2 (保留): anchor_state == EXPIRED 时早退
        # EXPIRED 意味着锚参照系本身不可用 (GEX 超过 gex_freshness_expired_ms),
        # 此时 ED1/center_loss 等中心性因子都是基于已过期的 flip_point 计算的,
        # 继续输出 H_time ≈ 0 导致 score≈0 / "濒危" 是伪精度——底层参照系已经失效,
        # 不是"锚很危险", 是"测量失效"。
        if anchor_state == "EXPIRED":
            return None, "未就绪", None

        # ── H_time: 分段时间常数 (FRESH 缓降 → STALE 指数衰减 → EXPIRED 归零)
        h_time = self._compute_h_time(anchor_source_ts_ms)

        # ── H_erosion: sigmoid (erosion_drift → [0, 1])
        erosion_drift = raw_evidence.get("erosion_drift")
        if erosion_drift is None:
            h_erosion = 0.5
        else:
            h_erosion = 1.0 / (1.0 + math.exp(
                CONFIG["health_erosion_beta"]
                * (erosion_drift - CONFIG["health_erosion_inflection"])))

        # ── H_center_loss: sigmoid (center_loss → [0, 1])
        center_loss = raw_evidence.get("center_loss")
        if center_loss is None:
            h_center_loss = 0.5
        else:
            h_center_loss = 1.0 / (1.0 + math.exp(
                CONFIG["health_center_loss_beta"]
                * (center_loss - CONFIG["health_center_loss_inflection"])))

        # ── H_space: 几何平均 (修正 B)
        h_space = math.sqrt(h_erosion * h_center_loss)

        # ── H_micro: PPE 百分位 sigmoid (低百分位 = 强吸收 = 高健康)
        ppe_percentile = raw_evidence.get("ppe_percentile")
        if ppe_percentile is None:
            h_micro = 0.5
        else:
            h_micro = 1.0 / (1.0 + math.exp(
                CONFIG["health_micro_beta"]
                * (ppe_percentile - 0.5)))

        # ── H_stability: RESETTING 期间爬升，其他状态 = 1.0
        if anchor_state == "RESETTING":
            stable_target = float(CONFIG["anchor_stable_bars"])
            stable_count  = float(anchor_stable_bar_count or 0)
            h_stability = min(1.0, stable_count / stable_target) if stable_target > 0 else 1.0
        else:
            h_stability = 1.0

        score = 100.0 * h_time * h_space * h_micro * h_stability
        # 数值保护: 浮点误差可能让分数略超 [0, 100]
        score = max(0.0, min(100.0, score))

        level = self._classify_health_level(score)

        breakdown = {
            "h_time":        h_time,
            "h_erosion":     h_erosion,
            "h_center_loss": h_center_loss,
            "h_space":       h_space,
            "h_micro":       h_micro,
            "h_stability":   h_stability,
        }
        return score, level, breakdown

    def _compute_h_time(self, anchor_source_ts_ms):
        """
        v1.3: 分段时间常数 H_time 计算。

        FRESH 期 (age < T_fresh):   线性从 1.0 降到 fresh_floor (默认 0.9)
        STALE 期 (T_fresh≤age<T_expired): 指数衰减，时间常数 τ = (T_expired-T_fresh)/3
        EXPIRED 期 (age ≥ T_expired): 归零

        两段交界 age = T_fresh 处连续 (左右均 = fresh_floor)，无 step-function。
        """
        if anchor_source_ts_ms is None:
            return 0.5  # 中性，未传入时不让健康度被拉偏

        t_fresh_ms    = CONFIG["gex_freshness_stale_ms"]
        t_expired_ms  = CONFIG["gex_freshness_expired_ms"]
        fresh_floor   = CONFIG["health_time_fresh_floor"]

        age_ms = now_ms() - anchor_source_ts_ms
        if age_ms < 0:
            age_ms = 0  # 时钟漂移保护

        if age_ms < t_fresh_ms:
            # FRESH 期: 1.0 → fresh_floor 线性下降
            progress = age_ms / float(t_fresh_ms)
            return 1.0 - (1.0 - fresh_floor) * progress
        elif age_ms < t_expired_ms:
            # STALE 期: fresh_floor × exp(-(age - T_fresh) / τ)
            tau = (t_expired_ms - t_fresh_ms) / 3.0
            if tau <= 0:
                return 0.0
            return fresh_floor * math.exp(-(age_ms - t_fresh_ms) / tau)
        else:
            return 0.0

    def _classify_health_level(self, score):
        """
        v1.3: 连续健康度分数 → 离散 UI 分档标签。
        仅用于状态栏展示，不进入任何决策路径。
        """
        if score is None:
            return "未就绪"
        if score >= CONFIG["health_level_healthy"]:
            return "优"
        if score >= CONFIG["health_level_solid"]:
            return "良"
        if score >= CONFIG["health_level_stressed"]:
            return "警"
        if score >= CONFIG["health_level_critical"]:
            return "危"
        return "濒危"


# ================================================================
# SECTION 8: MODULE 7 — SnapshotRecorder
# ================================================================

class SnapshotRecorder:
    """
    维护内存快照历史（三张逻辑表）。
    fmz 沙盒无需 SQLite，in-memory deque 足够观测使用。

    raw_values : 每柱一条，全部原始因子值
    labels     : 每柱一条，全部标签和状态
    events     : 状态迁移事件（脱离确认、缺口闭合、锚偏移）

    v1.2 增强:
      事件持久化 — 追加写入本地 jsonl 文件。
      写失败只 WARN，不影响主流程。FMZ 沙盒无写权限时降级为纯内存模式。
    """

    def __init__(self):
        max_size = CONFIG["snapshot_history_size"]
        self._raw_values = collections.deque(maxlen=max_size)
        self._labels     = collections.deque(maxlen=max_size)
        self._events     = collections.deque(maxlen=max_size)

        # v1.2: 持久化状态追踪
        self._persist_enabled  = bool(CONFIG.get("event_persist_enabled"))
        self._persist_path     = CONFIG.get("event_persist_path")
        self._persist_failures = 0
        self._persist_disabled_reason = None
        if self._persist_enabled and self._persist_path:
            log_info("SnapshotRecorder: event persistence enabled → "
                     + self._persist_path)

    def write(self, bar, anchor_freshness, anchor_shift_event,
              shift_magnitude, flip_point, band_half, band_clamped,
              dev_state, raw_evidence,
              labels, system_state, instructions,
              collection_metrics=None):
        """
        组装并存储一个快照帧，返回 snapshot dict 供 Display 使用。

        v1.2 新增参数:
          band_clamped: bool — 本柱 band_half 是否触发硬护栏截断

        v1.3.2 新增参数:
          collection_metrics: dict | None — 本轮采集度量，包含
            wall_time_ms 和 catchup_rounds 两个字段写入 raw_values。
            None 时字段填 None，不作伪值。

          只入 2 个字段（最少而够用）:
            poll_wall_time_ms       — 本轮 drain 挂钟耗时
            catchup_rounds_used     — 本轮 drain poll 次数（1=单次）

          不入的候选字段及理由:
            trade_count / bar_count : 可从 Bar 序列反推
            backlogged (bool)       : 衍生标签，在 Display 展示即可
            hit_wall_time / hit_limit: 过于底层，回放价值低
        """
        bar_index = bar["bar_index"]
        ts_ms     = now_ms()

        # v1.3.2: 从度量中抽取写入 raw_values 的两个字段
        if isinstance(collection_metrics, dict):
            poll_wall_time_ms   = collection_metrics.get("wall_time_ms")
            catchup_rounds_used = collection_metrics.get("catchup_rounds")
        else:
            poll_wall_time_ms   = None
            catchup_rounds_used = None

        raw_row = {
            "bar_index":             bar_index,
            "ts_ms":                 ts_ms,
            "price":                 bar["close"],
            "open":                  bar["open"],
            "high":                  bar["high"],
            "low":                   bar["low"],
            "flip_point":            flip_point,
            "band_half":             band_half,
            "band_clamped":          band_clamped,
            "anchor_freshness":      anchor_freshness,
            "anchor_shift_event":    anchor_shift_event,
            "shift_magnitude":       shift_magnitude,
            "normalized_deviation":  dev_state.get("normalized_deviation"),
            "outside_bar_count":     dev_state.get("outside_bar_count"),
            "ppe_raw":               raw_evidence.get("ppe_raw"),
            "ppe_is_spike":          raw_evidence.get("ppe_is_spike"),
            "ppe_percentile":        raw_evidence.get("ppe_percentile"),
            "ppe_short_median":      raw_evidence.get("ppe_short_median"),
            "ols_slope":             raw_evidence.get("ols_slope"),
            "r_squared":             raw_evidence.get("r_squared"),
            "cvd_direction":         raw_evidence.get("cvd_direction"),
            "cvd_strength":          raw_evidence.get("cvd_strength"),
            "cvd_degraded":          raw_evidence.get("cvd_degraded"),
            # v1.3 中心性原始因子
            "ed1_raw":               raw_evidence.get("ed1_raw"),
            "sign_consistency":      raw_evidence.get("sign_consistency"),
            "center_loss":           raw_evidence.get("center_loss"),
            "erosion_drift":         raw_evidence.get("erosion_drift"),
            "centrality_frozen":     raw_evidence.get("centrality_frozen"),
            "centrality_buffer_len": raw_evidence.get("centrality_buffer_len"),
            # v1.3.2 采集度量 (最少而够用)
            "poll_wall_time_ms":     poll_wall_time_ms,
            "catchup_rounds_used":   catchup_rounds_used,
        }
        self._raw_values.append(raw_row)

        label_row = {
            "bar_index":             bar_index,
            "ts_ms":                 ts_ms,
            "runtime_gate":          system_state.get("runtime_gate"),
            "anchor_state":          system_state.get("anchor_state"),
            "event_state":           system_state.get("event_state"),
            "classification_state":  system_state.get("classification_state"),
            "ols_label":             labels.get("ols_label"),
            "cvd_label":             labels.get("cvd_label"),
            "ppe_quality":           labels.get("ppe_quality"),
            "absorption_trend_tag":  labels.get("absorption_trend_tag"),
            "anchor_validity":       labels.get("anchor_validity"),
            "classification_result": labels.get("classification_result"),
            "confidence":            labels.get("confidence"),
            "reset_ols_window":      instructions.get("reset_ols_window"),
            "freeze_absorption":     instructions.get("freeze_absorption_trend"),
            "reset_deviation":       instructions.get("reset_deviation_counter"),
            "reset_ppe_history":     instructions.get("reset_ppe_history"),
            "reset_ppe_short":       instructions.get("reset_ppe_short_buffer"),
            # v1.3 健康度与侵蚀方向
            "erosion_side":          labels.get("erosion_side"),
            "anchor_health_score":   labels.get("anchor_health_score"),
            "anchor_health_level":   labels.get("anchor_health_level"),
        }
        self._labels.append(label_row)

        self._record_events(
            bar_index, ts_ms, dev_state, anchor_shift_event,
            shift_magnitude, labels)

        return {"raw": raw_row, "labels": label_row}

    def _record_events(self, bar_index, ts_ms, dev_state,
                        anchor_shift_event, shift_magnitude, labels):
        """
        事件仅在状态转折点记录一次。

        v1.2 修正:
          - departure_confirmed 事件使用 DeviationTracker 新增的
            departure_confirmed_event 标记，消除 REENTRY_PENDING →
            CONFIRMED 回跳时的重复触发。
          - 每个事件追加到本地 jsonl 文件（若启用）。
        """
        if dev_state.get("gap_closure_event"):
            event = {
                "bar_index":  bar_index,
                "ts_ms":      ts_ms,
                "event_type": "gap_closure_confirmed",
                "detail": {
                    "classification_at_closure": labels.get("classification_result"),
                    "confidence_at_closure":     labels.get("confidence"),
                },
            }
            self._events.append(event)
            self._persist_event(event)

        if anchor_shift_event:
            event = {
                "bar_index":  bar_index,
                "ts_ms":      ts_ms,
                "event_type": "anchor_shift",
                "detail": {
                    "shift_magnitude": shift_magnitude,
                },
            }
            self._events.append(event)
            self._persist_event(event)

        # v1.2: 使用 DeviationTracker 的一次性事件标记
        if dev_state.get("departure_confirmed_event"):
            event = {
                "bar_index":  bar_index,
                "ts_ms":      ts_ms,
                "event_type": "departure_confirmed",
                "detail": {
                    "normalized_deviation": dev_state.get("normalized_deviation"),
                },
            }
            self._events.append(event)
            self._persist_event(event)

    def _persist_event(self, event):
        """
        v1.2: 追加事件到本地 jsonl 文件。
        设计原则:
          - 只写不读，不阻塞主循环（单行 JSON，小 I/O）
          - 写失败只 WARN，持续失败 3 次后自动禁用
          - FMZ 沙盒无写权限时降级为纯内存模式
        """
        if not self._persist_enabled or not self._persist_path:
            return
        if self._persist_disabled_reason is not None:
            return  # 之前失败过，已禁用

        try:
            line = json.dumps(event, ensure_ascii=False)
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            # 成功写入后重置失败计数
            if self._persist_failures > 0:
                log_info("SnapshotRecorder: event persist recovered")
                self._persist_failures = 0
        except Exception as error:
            self._persist_failures += 1
            log_warn("SnapshotRecorder: event persist failed ("
                     + str(self._persist_failures) + "): " + str(error))
            if self._persist_failures >= 3:
                self._persist_disabled_reason = str(error)
                log_warn("SnapshotRecorder: event persist DISABLED after 3 failures")

    def get_recent_events(self, n=10):
        return list(self._events)[-n:]


# ================================================================
# SECTION 9: DISPLAY
# ================================================================

class Display:
    """
    将 Alpha Radar 状态渲染到 fmz 平台输出。
    模式参考 gamma_spatial_observer_v6.py。

    v1.2 重构 (Chart Flag 事件驱动 + 状态栏事件流表)

    v1.3.3 任务调度边界收口:
      本类明确区分两种调度模式, 入口与分类一一对应 ——

      ┌────────────────────────────────────────────────────────┐
      │ Event-driven (由 on_bar 调度, 事件触发):                │
      │   - 更新 latest_* 缓存 (每根新 Bar 都刷新)             │
      │   - 入队 pending_chart_queue (按真实 bar.ts_ms 顺序)   │
      │   - Chart Flags (三类离散事件)                          │
      │   - 状态变化去重日志 _maybe_emit_state_log              │
      ├────────────────────────────────────────────────────────┤
      │ Time-driven (由 run() 周期调度, 挂钟触发):              │
      │   - tick_chart()      — chart 线条刷新 (消费 pending)   │
      │   - tick_status()     — 状态栏六表刷新                  │
      │   - tick_logprofit()  — LogProfit 输出 health_score     │
      │   - tick_summary()    — 综述日志                        │
      └────────────────────────────────────────────────────────┘

      核心设计动机:
        v1.3.2 之前, chart/status/summary/logprofit 四个任务虽然各自
        有 interval 参数, 但全部挂在 on_bar() 下面。由于 on_bar 只在
        新 Bar 到来时被调用, 这些任务的真实调度是"新 Bar 到来 AND 过
        了 interval"的合取, 不是周期性 —— 导致实盘观察到 summary 间
        隔从 78s 拉到 380s。v1.3.3 把 time-driven 任务从 on_bar 中剥
        离, 交给 run() 级别的挂钟调度器。

        Event-driven 任务保持不动 —— 它们的语义本就是"事件来了才做"。

      关于"不伪造数据"的纪律:
        - pending_chart_queue 只缓存真实完成的 Bar (来自 on_bar)
        - tick_chart 清空队列后, 队列为空时不会 add 任何点
        - tick_logprofit 在 health_score=None 时跳过, 不伪造 0
        - tick_summary 在 latest_snapshot 尚未就绪时跳过
        - tick_status 总是刷新 (状态栏重绘本身不伪造数据, 所有显示
          值都从最新缓存或实时 getter 取, 缺值时显示 "-" 或 "未就绪")
    """

    # 事件类型 → Flag 显示映射 (v1.2)
    FLAG_MAP = {
        "departure_confirmed":    ("脱离", "脱离确认"),
        "gap_closure_confirmed":  ("回补", "缺口回补"),
        "anchor_shift":           ("锚移", "锚迁移"),
    }

    # 事件类型 → 中文标签 (状态栏用)
    EVENT_TYPE_ZH = {
        "departure_confirmed":    "脱离确认",
        "gap_closure_confirmed":  "缺口回补",
        "anchor_shift":           "锚迁移",
    }

    def __init__(self):
        self._chart_object          = None
        self._chart_initialized     = False
        self._last_chart_update_sec = 0
        self._last_status_update_sec = 0   # v1.3.3 新增: 状态栏 time-driven 计时器
        self._last_logprofit_sec    = 0
        self._last_summary_sec      = 0
        self._last_state_log_key    = None
        # v1.2: Flag 去重键 — 同一 bar_index 的事件不重复标
        self._last_flagged_bar_index = -1

        # v1.3.3: latest 缓存 —— on_bar() 只负责更新此缓存, time-driven
        # 任务读取缓存生成输出。缓存初始为 None, 所有 tick_*() 方法
        # 在 None 状态下必须优雅跳过, 不伪造数据。
        self._latest_snapshot     = None
        self._latest_dev_state    = None
        self._latest_labels       = None
        self._latest_system_state = None

        # v1.3.3: pending_chart_queue —— on_bar() 把新 Bar 的 chart row
        # 入队, tick_chart() 按 bar.ts_ms 顺序 flush。队列只存真实 Bar,
        # 从不放墙钟时刻点。flush 后队列清空。
        # 用 list 而不是 deque: flush 一次性消费全部, 顺序遍历即可。
        self._pending_chart_queue = []

    def init(self):
        exchange_obj = globals().get("exchange")
        if exchange_obj and hasattr(exchange_obj, "SetPrecision"):
            try:
                exchange_obj.SetPrecision(2, 4)
            except Exception:
                pass

    # ════════════════════════════════════════════════════════════
    # Event-driven 入口 (新 Bar 完成时由 run() 调用)
    # ════════════════════════════════════════════════════════════

    def on_bar(self, snapshot, anchor_ctx, bar_asm, dev_state, labels,
               system_state, snap_rec):
        """
        v1.3.3 重构: 仅处理事件驱动任务, 不再直接触发 chart/status/
        summary/logprofit 的输出 —— 这些任务转交给 run() 级别的
        time-driven 调度器 (tick_* 方法)。

        本方法保留的职责:
          1) 刷新 latest_* 缓存 (供 tick_* 方法消费)
          2) 入队 pending_chart_queue (供 tick_chart 消费)
          3) Chart Flags (事件驱动, 本来就该在这里)
          4) 状态变化去重日志 (事件驱动)

        注: snap_rec 参数保留以兼容旧签名, 实际在本方法不被消费
            (事件流表刷新由 tick_status 读取)。
        """
        raw_row = snapshot.get("raw", {}) if snapshot else {}

        # 1) 更新 latest 缓存 —— 供 time-driven 任务消费
        self._latest_snapshot     = snapshot
        self._latest_dev_state    = dev_state
        self._latest_labels       = labels
        self._latest_system_state = system_state

        # 2) 入队 pending chart row —— time-driven 的 tick_chart 会 flush
        # 只入队有效 Bar 点 (price 和 ts_ms 都存在)
        price     = raw_row.get("price")
        bar_ts_ms = raw_row.get("ts_ms")
        if price is not None and bar_ts_ms is not None:
            self._pending_chart_queue.append(raw_row)

        # 3) 事件驱动: Chart Flags (三类离散事件, 按真实 bar.ts_ms 打标)
        self._draw_chart_flags_if_any(raw_row, dev_state, labels, system_state)

        # 4) 事件驱动: 状态变化去重日志 (三元组 key 未变则不打)
        self._maybe_emit_state_log(labels, system_state)

    # ════════════════════════════════════════════════════════════
    # Time-driven 入口 (由 run() 按 interval 调用)
    # ════════════════════════════════════════════════════════════

    def tick_chart(self, anchor_ctx=None, bar_asm=None):
        """
        v1.3.3: 固定时间任务 —— chart 线条刷新。

        行为:
          1) 若 chart 未初始化且有待输出数据, 做一次初始化
          2) 按真实 bar.ts_ms 顺序 flush pending_chart_queue 的全部 Bar
          3) queue 为空时 return, 不 add 任何点 (不伪造 wall-clock 点)

        调用频率: 由 run() 按 chart_update_interval_sec 触发。
        实际行为: 即使本 interval 没有新 Bar 进入 queue, 也不会重复
        add 上次的点 —— 只有真实 Bar 才会进入队列, 无 Bar 即无 flush。
        """
        now_sec = int(time.time())
        if now_sec - self._last_chart_update_sec < CONFIG["chart_update_interval_sec"]:
            return
        # 无论本次是否真的 flush, 都推进计时器避免瞬时高频重入
        self._last_chart_update_sec = now_sec

        if not self._pending_chart_queue:
            return  # 没有待输出 Bar, 不做任何事 —— 纪律: 不伪造

        chart_fn = _get_fmz_function("Chart")
        if not chart_fn:
            self._pending_chart_queue = []  # fmz 环境缺失时丢弃, 防止无限堆积
            return

        # 初始化 chart 对象 (仅首次)
        try:
            if not self._chart_initialized:
                self._init_chart_object(chart_fn)

            if not (self._chart_object and hasattr(self._chart_object, "add")):
                self._pending_chart_queue = []
                return

            # Flush 队列, 按 ts_ms 递增顺序逐点 add
            # (队列本身是按入队顺序 = 按 bar_index 顺序, 等同于 ts_ms 递增)
            for row in self._pending_chart_queue:
                self._add_one_chart_row(row)

            # flush 完成后清空队列
            self._pending_chart_queue = []

        except Exception as error:
            log_warn("Display tick_chart failed: " + str(error))
            # 即使出错也清空队列, 避免同一批点在下次 tick 被重复尝试 add
            self._pending_chart_queue = []

    def tick_status(self, anchor_ctx, bar_asm, snap_rec, cycle_index):
        """
        v1.3.3: 固定时间任务 —— 状态栏六表刷新。

        行为:
          每 status_update_interval_sec 秒刷新一次。
          即使没有新 Bar 也要刷新 —— 状态栏展示的是"当下系统状态",
          包括 GEX 采集时间、未完成柱进度、累计计数器等 wall-clock 信息。

        "不伪造数据"如何兑现:
          - 若 latest_snapshot 为 None (尚无任何 Bar 完成), 状态栏
            中"因子证据"等以 Bar 为单位的字段全部显示 "-" 或未就绪
          - 采集时效性 / 数据采集 表的值来自 bar_asm / anchor_ctx
            的实时 getter, 这些是 wall-clock 计数器, 本就随时间更新,
            不算伪造
        """
        now_sec = int(time.time())
        if now_sec - self._last_status_update_sec < CONFIG["status_update_interval_sec"]:
            return
        self._last_status_update_sec = now_sec

        # 从 latest 缓存取; 尚未就绪时使用空壳 dict 让渲染方法兜底
        snapshot     = self._latest_snapshot
        labels       = self._latest_labels       or {}
        system_state = self._latest_system_state or {}
        raw_row      = snapshot.get("raw", {}) if snapshot else {}

        self._render_status(raw_row, labels, system_state,
                            anchor_ctx, bar_asm, snap_rec, cycle_index)

    def tick_logprofit(self):
        """
        v1.3.3: 固定时间任务 —— LogProfit 输出锚健康度。

        行为:
          每 logprofit_interval_sec 秒尝试输出一次。
          score=None 时跳过, 不伪造 0, 不复用旧值。

        为什么 LogProfit 也转为 time-driven:
          LogProfit 绘制的是时间序列曲线, 采样应当是挂钟均匀的 ——
          否则横轴"时间"语义就被 bar 生成节奏污染。score=None 时产生
          断点是正确的视觉语义。
        """
        now_sec = int(time.time())
        if now_sec - self._last_logprofit_sec < CONFIG["logprofit_interval_sec"]:
            return

        labels = self._latest_labels or {}
        score  = labels.get("anchor_health_score")

        # score=None 跳过, 不伪造 —— 计时器推进但不写曲线
        # (推进计时器防止每 tick 都重入判断, 下次间隔到再重试)
        self._last_logprofit_sec = now_sec
        if score is None:
            return

        logprofit_fn = _get_fmz_function("LogProfit")
        if logprofit_fn:
            try:
                logprofit_fn(round(float(score), 2))
            except Exception:
                pass

    def tick_summary(self):
        """
        v1.3.3: 固定时间任务 —— 综述日志。

        行为:
          每 summary_log_interval_sec 秒输出一条综述。
          基于 latest 缓存 —— 若尚无快照则跳过, 不伪造。
          若最新快照挂钟时间已经很旧 (例如低流速下 Bar 间隔数分钟),
          仍然输出, 但时间信息来自快照本身 —— 观察者通过对比 raw_row
          的 ts_ms 和综述输出时刻, 可以判断数据新旧。
        """
        now_sec = int(time.time())
        if now_sec - self._last_summary_sec < CONFIG["summary_log_interval_sec"]:
            return
        self._last_summary_sec = now_sec

        snapshot = self._latest_snapshot
        if snapshot is None:
            return  # 尚无任何 Bar 完成过

        labels  = self._latest_labels or {}
        raw_row = snapshot.get("raw", {})
        log_info(
            "综述: 价={} 偏差={}σ 锚={} PPE%={} OLS={} CVD={}".format(
                fmt_price(raw_row.get("price")),
                fmt_number(raw_row.get("normalized_deviation"), 2),
                labels.get("anchor_validity", "-"),
                fmt_percent(raw_row.get("ppe_percentile")),
                labels.get("ols_label", "-"),
                labels.get("cvd_label", "-"),
            )
        )

    # ════════════════════════════════════════════════════════════
    # Chart 底层原语 (被 tick_chart 和 on_bar 共同使用)
    # ════════════════════════════════════════════════════════════

    def _init_chart_object(self, chart_fn):
        """
        v1.3.3: chart 对象一次性初始化。
        从老 _update_chart 里的"if not initialized"分支提取而来, 不改配置。
        """
        chart_config = {
            "title": {"text": "Alpha Radar v1.3"},
            "xAxis": {"type": "datetime"},
            "yAxis": [
                {"title": {"text": "价格 (USD)"}, "opposite": False},
                {
                    "title": {"text": "归一化偏差 (σ)"},
                    "opposite": True,
                    "plotLines": [
                        {"value":  1, "color": "#FF9800",
                         "dashStyle": "ShortDash", "width": 1},
                        {"value": -1, "color": "#FF9800",
                         "dashStyle": "ShortDash", "width": 1},
                    ],
                },
            ],
            "series": [
                {"id": "price",   "name": "成交价",
                 "data": [], "yAxis": 0, "color": "#2196F3"},
                {"name": "带上沿", "data": [],
                 "dashStyle": "ShortDash", "yAxis": 0,
                 "color": "#FF9800", "lineWidth": 1},
                {"name": "带下沿", "data": [],
                 "dashStyle": "ShortDash", "yAxis": 0,
                 "color": "#FF9800", "lineWidth": 1},
                {"name": "flip",  "data": [],
                 "dashStyle": "Dot", "yAxis": 0,
                 "color": "#9C27B0", "lineWidth": 1},
                {"name": "偏差(σ)", "data": [],
                 "yAxis": 1, "color": "#F44336", "lineWidth": 1},
                {"type": "flags", "name": "事件",
                 "onSeries": "price", "data": []},
            ],
        }
        self._chart_object = chart_fn(chart_config)
        if self._chart_object and hasattr(self._chart_object, "reset"):
            self._chart_object.reset()
        self._chart_initialized = True

    def _add_one_chart_row(self, raw_row):
        """
        v1.3.3: 把单根真实 Bar 的点加到 chart 各 series。
        被 tick_chart flush 队列时逐 row 调用。

        入参 raw_row 必须是真实 Bar 的 row (含 ts_ms 和 price),
        tick_chart 在调用本方法前已过滤非法 row。
        """
        price      = raw_row.get("price")
        bar_ts_ms  = raw_row.get("ts_ms")
        flip_point = raw_row.get("flip_point")
        band_half  = raw_row.get("band_half")
        nd         = raw_row.get("normalized_deviation")

        self._chart_object.add(0, [bar_ts_ms, price])
        if flip_point is not None and band_half is not None:
            self._chart_object.add(1, [bar_ts_ms, flip_point + band_half])
            self._chart_object.add(2, [bar_ts_ms, flip_point - band_half])
            self._chart_object.add(3, [bar_ts_ms, flip_point])
        if nd is not None:
            self._chart_object.add(4, [bar_ts_ms, nd])

    def _draw_chart_flags_if_any(self, raw_row, dev_state, labels, system_state):
        """
        v1.3.3: 事件驱动的 Flag 打标入口, 仅 on_bar 调用。

        为避免在 chart 对象尚未初始化时丢失 flag, 这里会在需要时做
        lazy init。但对"线条刷新"保持纪律: 不借此机会 add 任何线条点。

        "lazy init 是否违反 time-driven 纪律":
          Flags 本就是 event-driven, 事件发生时就应打标。若等 tick_chart
          触发后才初始化 chart, 首次 Flag 可能丢失。所以 lazy init 是
          event-driven 任务的合理副作用, 不影响 tick_chart 的 time-driven
          调度 —— tick_chart 再次进入时发现已初始化, 正常 flush 即可。
        """
        bar_index = raw_row.get("bar_index", -1)
        bar_ts_ms = raw_row.get("ts_ms")
        if bar_ts_ms is None:
            return

        chart_fn = _get_fmz_function("Chart")
        if not chart_fn:
            return

        try:
            if not self._chart_initialized:
                self._init_chart_object(chart_fn)

            if not (self._chart_object and hasattr(self._chart_object, "add")):
                return

            self._maybe_add_event_flag(
                bar_index, bar_ts_ms, dev_state, labels, system_state,
                raw_row.get("anchor_shift_event", False),
                raw_row.get("shift_magnitude"))

        except Exception as error:
            log_warn("Display draw_chart_flags failed: " + str(error))

    def _maybe_add_event_flag(self, bar_index, bar_ts_ms,
                               dev_state, labels, system_state,
                               anchor_shift_event, shift_magnitude):
        """
        事件驱动的 Flag 打标。

        v1.2 规则:
          1. 同一 bar_index 只打一次 (去重键)
          2. 仅三类离散事件触发: departure_confirmed / gap_closure / anchor_shift
          3. 分类事件需要置信度 HIGH 或 MEDIUM (LOW/PARTIAL 不打)
          4. 锚迁移无论置信度都打（本身是结构性事件）

        v1.3.1 Fix-1:
          补齐 anchor_shift 分支。旧实现只覆盖 departure_confirmed 和
          gap_closure_confirmed 两类，FLAG_MAP 里的 "anchor_shift" 条目是死代码，
          规范定义的三类事件 Flag 覆盖不完整。现在通过新增参数 anchor_shift_event
          和 shift_magnitude 由调用方显式传入，在本函数内触发锚迁移 Flag。

        事件优先级 (同柱多事件时取其一，避免重复打):
          departure_confirmed > gap_closure_confirmed > anchor_shift
          物理依据: 脱离确认是最前端事件；回补确认其次；锚迁移是外部结构变化，
          若与前两者同柱发生，取前两者之一即可表达本柱的主要语义。
        """
        if bar_index == self._last_flagged_bar_index:
            return  # 同一 Bar 已打过

        # 确定事件类型（按优先级）
        event_type = None
        extra_detail = None  # 给 anchor_shift 用的 magnitude 透传

        if dev_state.get("departure_confirmed_event"):
            event_type = "departure_confirmed"
        elif dev_state.get("gap_closure_event"):
            event_type = "gap_closure_confirmed"
        elif anchor_shift_event:
            event_type = "anchor_shift"
            extra_detail = shift_magnitude

        if event_type is None:
            return

        # 置信度过滤（分类类事件）
        # - departure_confirmed: 脱离确认时分类结论可能还没到 AVAILABLE，无条件打
        # - gap_closure_confirmed: 回补时有分类，仅 HIGH/MEDIUM 打
        # - anchor_shift: 结构性事件，无条件打
        confidence = (labels or {}).get("confidence") or ""
        if event_type == "gap_closure_confirmed":
            if confidence not in ("HIGH", "MEDIUM"):
                return

        flag_info = self.FLAG_MAP.get(event_type)
        if not flag_info:
            return
        flag_title, flag_text_base = flag_info

        # 细节丰富化
        if event_type == "anchor_shift":
            # 锚迁移的细节是 magnitude，不涉及分类结论
            if extra_detail is not None:
                flag_text = flag_text_base + " Δ=" + fmt_number(extra_detail, 2)
            else:
                flag_text = flag_text_base
        else:
            classification_result = (labels or {}).get("classification_result") or ""
            if classification_result and classification_result != "部分证据":
                flag_text = flag_text_base + " / " + classification_result
                if confidence:
                    flag_text += " (" + confidence + ")"
            else:
                flag_text = flag_text_base

        try:
            self._chart_object.add(5, {
                "x":     bar_ts_ms,
                "title": flag_title,
                "text":  flag_text,
            })
            self._last_flagged_bar_index = bar_index
        except Exception as error:
            log_warn("Display flag add failed: " + str(error))

    def _render_status(self, raw_row, labels, system_state,
                       anchor_ctx, bar_asm, snap_rec, cycle_index):
        """
        v1.3.3: 状态栏六表渲染主体。
        由 tick_status() 调用, cycle_index 来自 run() 的主循环计数器。

        raw_row 可能为空 dict (系统还没收到过任何 Bar), 下游各字段
        读取必须用 .get() 带 default, 渲染时对 None 统一显示 "-"。

        v1.3.3 调整:
          - 新增 cycle_index 参数, 供"数据采集"表的"运行轮次"行使用
          - 采集时效性表 (2 行) → 数据采集表 (5 行)
          - 其余五张表保持不变
        """
        price               = raw_row.get("price")
        flip_point          = raw_row.get("flip_point")
        band_half           = raw_row.get("band_half")
        nd                  = raw_row.get("normalized_deviation")
        outside_bar_count   = raw_row.get("outside_bar_count", 0)
        bar_index           = raw_row.get("bar_index", 0)
        band_clamped        = raw_row.get("band_clamped", False)

        band_upper = (flip_point + band_half) if (flip_point and band_half) else None
        band_lower = (flip_point - band_half) if (flip_point and band_half) else None
        band_width = (2 * band_half) if band_half else None

        classification_result = (labels.get("classification_result") or "-") if labels else "-"
        confidence            = (labels.get("confidence") or "-") if labels else "-"
        event_state           = system_state.get("event_state", "-")

        summary = (
            "Alpha Radar v1.3.3 | 价={} | 偏差={}σ | {} | 分类={} ({})".format(
                fmt_price(price),
                fmt_number(nd, 2),
                event_state,
                classification_result,
                confidence,
            )
        )

        labels_safe = labels or {}

        # 带宽单元格: 如果触发了 clamp，添加标注
        band_half_cell = fmt_price(band_half)
        if band_clamped:
            band_half_cell = band_half_cell + " [CLAMP]"

        table_anchor = {
            "type":  "table",
            "title": "空间锚",
            "cols":  ["字段", "值", "说明"],
            "rows":  [
                ["Gamma 中轴",   fmt_price(flip_point),   labels_safe.get("anchor_validity", "-")],
                ["吸收带半宽",   band_half_cell,          "±USD"],
                ["带上沿",       fmt_price(band_upper),   ""],
                ["带下沿",       fmt_price(band_lower),   ""],
                ["带总宽",       fmt_number(band_width, 0), "USD"],
                ["锚新鲜度",     anchor_ctx.get_freshness(), ""],
                ["数据时间",     fmt_timestamp_ms(anchor_ctx.get_source_ts_ms()), ""],
                ["弹簧系数",     fmt_number(anchor_ctx.get_spring(), 6), "BTC/USD"],
                ["吸收趋势",     labels_safe.get("absorption_trend_tag", "-"), ""],
            ],
        }

        table_factors = {
            "type":  "table",
            "title": "因子证据",
            "cols":  ["因子", "值", "标签"],
            "rows":  [
                ["归一化偏差",  fmt_number(nd, 3),
                 event_state],
                ["Outside柱数", str(outside_bar_count),    ""],
                ["PPE",         fmt_number(raw_row.get("ppe_raw"), 3),
                 labels_safe.get("ppe_quality", "-")],
                ["PPE 百分位",  fmt_percent(raw_row.get("ppe_percentile")),    ""],
                ["PPE 短中位",  fmt_number(raw_row.get("ppe_short_median"), 3), ""],
                ["OLS 斜率",    fmt_number(raw_row.get("ols_slope"), 5),
                 labels_safe.get("ols_label", "-")],
                ["R²",          fmt_number(raw_row.get("r_squared"), 3),       ""],
                ["CVD 方向",    str(raw_row.get("cvd_direction", 0)),
                 labels_safe.get("cvd_label", "-")],
                ["CVD 强度",    fmt_number(raw_row.get("cvd_strength"), 3),    ""],
                ["Bar 序号",    str(bar_index),
                 "总柱=" + str(bar_asm.bar_count())],
            ],
        }

        table_state = {
            "type":  "table",
            "title": "状态机",
            "cols":  ["轴", "状态"],
            "rows":  [
                ["运行门",   system_state.get("runtime_gate", "-")],
                ["锚状态",   system_state.get("anchor_state", "-")],
                ["脱离状态", system_state.get("event_state", "-")],
                ["分类可用", system_state.get("classification_state", "-")],
                ["分类结果", classification_result],
                ["置信度",   confidence],
            ],
        }

        # v1.2: 事件流表
        table_events = self._build_event_stream_table(snap_rec)

        # v1.3: 带内结构健康评估表
        table_health = self._build_health_table(raw_row, labels, system_state)

        # v1.3.3: 数据采集表 (原"采集时效性", 2 行 → 5 行完整诊断)
        table_data_collection = self._build_data_collection_table(
            raw_row, bar_asm, anchor_ctx, cycle_index)

        log_status(summary, tables=[table_anchor, table_factors,
                                     table_state, table_events,
                                     table_health, table_data_collection])

    def _build_health_table(self, raw_row, labels, system_state):
        """
        构造《带内结构健康评估》状态栏表。

        三列设计:
          指标   : 固定文字
          当前值 : 关键数值/标签
          说明   : 分解信息或上下文

        三行必须包含:
          1) 锚健康度 — 总分 + 等级 + 四因子分解
          2) 中心性分解 — ED1/sign_consistency/center_loss + 侵蚀方向
          3) 更新状态 — 冻结状态 + 窗口就绪度 + event_state + d_cap

        v1.3.2 展示语义三态收口 (E-2):
          ┌──────────────┬─────────────────────────────────────────┐
          │ 正常可计算值 │ 直接显示数值，如 "0.42 / 良"             │
          │ 未就绪       │ 显示 level 文本，如 "未就绪"（有业务语义）│
          │               │ 来源: COLD_START 或 EXPIRED             │
          │ 普通缺值     │ 显示 "-"（数据确实缺失，无业务语义）     │
          └──────────────┴─────────────────────────────────────────┘
          三类语义不混用，观察者能明确区分"系统不能算"和"数据碰巧没有"。
        """
        labels_safe = labels or {}
        raw_safe    = raw_row or {}
        sys_safe    = system_state or {}

        # ── 行 1: 锚健康度总分 + 四因子分解 ──
        score = labels_safe.get("anchor_health_score")
        level = labels_safe.get("anchor_health_level", "-")
        # v1.3.1 Fix-2: score=None 时（COLD_START 或 EXPIRED）明确显示 level 文本，
        # 避免状态栏只显示 "-" 让观察者误以为是数据缺失；"未就绪" 是一个明确语义。
        if score is None:
            score_cell = level if level else "-"
        else:
            score_cell = "{:.1f} / {}".format(score, level)

        # v1.3.1 Fix-4: 直接使用 LabelGenerator._compute_anchor_health 副产出的
        # breakdown（通过 labels["anchor_health_breakdown"] 传入），状态栏不重算。
        # 本分解数据是 UI 用途，不进入任何决策路径，也不计入快照字段。
        h_breakdown = labels_safe.get("anchor_health_breakdown")
        if isinstance(h_breakdown, dict):
            breakdown_str = "Ht={:.2f} Hs={:.2f} Hm={:.2f} Hst={:.2f}".format(
                h_breakdown.get("h_time", 0.0),
                h_breakdown.get("h_space", 0.0),
                h_breakdown.get("h_micro", 0.0),
                h_breakdown.get("h_stability", 0.0))
        else:
            breakdown_str = "-"

        row_health = ["锚健康度", score_cell, breakdown_str]

        # ── 行 2: 中心性分解 ──
        ed1        = raw_safe.get("ed1_raw")
        sign_cons  = raw_safe.get("sign_consistency")
        c_loss     = raw_safe.get("center_loss")
        erosion    = raw_safe.get("erosion_drift")
        erosion_side = labels_safe.get("erosion_side", "-")

        if ed1 is None or sign_cons is None or c_loss is None:
            centrality_cell = "-"
        else:
            centrality_cell = "ED1={:+.2f} 一致={:.2f} 离心={:.2f}".format(
                ed1, sign_cons, c_loss)

        if erosion is None:
            erosion_detail = "-"
        else:
            erosion_detail = "侵蚀={:.2f} / {}".format(erosion, erosion_side)

        row_centrality = ["中心性分解", centrality_cell, erosion_detail]

        # ── 行 3: 更新状态 ──
        frozen = raw_safe.get("centrality_frozen")
        if frozen is True:
            frozen_str = "是"
        elif frozen is False:
            frozen_str = "否"
        else:
            frozen_str = "-"

        buffer_len    = raw_safe.get("centrality_buffer_len", "-")
        window_target = CONFIG["centrality_window"]
        if buffer_len == "-":
            window_str = "-"
        else:
            window_str = "{}/{}".format(buffer_len, window_target)

        update_cell = "冻结={}  窗口={}".format(frozen_str, window_str)
        update_detail = "event={}  d_cap={:.2f}".format(
            sys_safe.get("event_state", "-"),
            CONFIG["centrality_d_cap"])

        row_update = ["更新状态", update_cell, update_detail]

        return {
            "type":  "table",
            "title": "带内结构健康评估",
            "cols":  ["指标", "当前值", "说明"],
            "rows":  [row_health, row_centrality, row_update],
        }

    def _build_data_collection_table(self, raw_row, bar_asm, anchor_ctx, cycle_index):
        """
        v1.3.3 《数据采集》状态栏表 (替代 v1.3.2 的《采集时效性》2 行表)。

        回答的核心问题: 观察者看到的是实时市场，还是滞后的旧市场？
        与 v1.3.2 的区别: 从 2 行扩展到 5 行, 补齐:
          - 成交量柱构造进度 (未完成柱 × 当前积累 / 目标)
          - 数据获取频率 (分别展示 aggTrades 和 GEX)
          - API 调用次数 (本轮 + 累计)
          - 上次数据获取时间 (完整 UTC+8 日期时间)
          - 运行轮次 (主循环 cycle_index)

        关键语义区分 (延续 v1.3.2):
          backlog  ≠  trade_id gap
          ────────────────────────────────
          backlog:      系统处理旧成交, trade_id 仍连续
          trade_id gap: REST 单轮装不下, 中间成交丢失, CVD 被动降级

        五行设计:
          1) 成交量柱构造 — 未完成柱进度 + 累计柱数
          2) aggTrades 采集 — 频率 + 本轮调用 + 上次成功 + 累计
          3) GEX 采集      — 频率 + 本轮触发 + 上次成功 + 累计 + freshness
          4) Backlog / 完整性 — backlog 状态 + CVD 降级
          5) 运行轮次 — cycle_index + bar_index + 总柱

        入参 cycle_index 由 run() 的主循环计数器提供。
        """
        raw_safe = raw_row or {}

        # ── 行 1: 成交量柱构造 ────────────────────────────────────
        # bar_asm.get_current_bar_build_progress() 返回未完成柱的瞬时进度,
        # 与快照字段独立 (快照只记录已完成柱)。
        try:
            progress = bar_asm.get_current_bar_build_progress()
        except Exception:
            progress = None

        if progress is None or progress.get("target_volume", 0) <= 0:
            bar_build_cell   = "-"
            bar_build_detail = "-"
        else:
            current = progress["current_volume"]
            target  = progress["target_volume"]
            ratio   = progress["ratio"] * 100.0
            bar_build_cell = "{} / {} BTC ({:.1f}%)".format(
                fmt_number(current, 3),
                fmt_number(target, 2),
                ratio)
            total_bars   = bar_asm.bar_count()
            cycle_bars   = raw_safe.get("catchup_rounds_used")
            # raw_safe 里没有"本轮产出 bar 数", 从 metrics 取更权威
            try:
                cycle_new_bars = bar_asm.get_last_cycle_metrics().get("bar_count", "-")
            except Exception:
                cycle_new_bars = "-"
            bar_build_detail = "已完成柱={} | 本轮新柱={}".format(
                total_bars, cycle_new_bars)

        row_bar_build = ["成交量柱构造", bar_build_cell, bar_build_detail]

        # ── 行 2: aggTrades 采集 ──────────────────────────────────
        # 频率目标: loop_sleep_sec (每轮至少 1 次 poll; drain 时多次)
        # 本轮调用: 来自 raw_safe.catchup_rounds_used 或 bar_asm metrics
        # 上次成功: bar_asm.get_last_success_fetch_ms
        # 累计:     bar_asm.get_poll_call_count_total
        loop_sec = CONFIG["loop_sleep_sec"]
        try:
            rounds_used      = raw_safe.get("catchup_rounds_used")
            if rounds_used is None:
                # raw_row 没有快照时, 回退到 metrics (状态栏可能在首 Bar 之前就开始刷新)
                rounds_used = bar_asm.get_last_cycle_metrics().get("catchup_rounds", 0)
            total_polls      = bar_asm.get_poll_call_count_total()
            last_success_ms  = bar_asm.get_last_success_fetch_ms()
        except Exception:
            rounds_used = 0
            total_polls = 0
            last_success_ms = 0

        agg_freq_cell = "每 {:.1f}s | 本轮×{}".format(float(loop_sec), rounds_used)
        if last_success_ms:
            agg_last_str = fmt_datetime_ms_utc8(last_success_ms)
        else:
            agg_last_str = "-"
        agg_detail = "上次成功 {} | 累计调用 {}".format(agg_last_str, total_polls)

        row_agg = ["aggTrades 采集", agg_freq_cell, agg_detail]

        # ── 行 3: GEX 采集 ────────────────────────────────────────
        # 频率目标: 最小 gex_min_fetch_interval_ms
        # 本轮触发: 0/1 由 anchor_ctx.was_fetch_attempted_this_cycle
        # 上次成功: anchor_ctx.get_last_fetch_success_ms
        gex_min_interval_sec = CONFIG["gex_min_fetch_interval_ms"] / 1000.0
        try:
            gex_attempted = anchor_ctx.was_fetch_attempted_this_cycle()
            gex_total_att = anchor_ctx.get_fetch_attempt_count_total()
            gex_total_ok  = anchor_ctx.get_fetch_success_count_total()
            gex_last_ok   = anchor_ctx.get_last_fetch_success_ms()
            gex_freshness = anchor_ctx.get_freshness()
        except Exception:
            gex_attempted = False
            gex_total_att = 0
            gex_total_ok  = 0
            gex_last_ok   = 0
            gex_freshness = "-"

        gex_freq_cell = "最小 {:.0f}s | 本轮×{}".format(
            gex_min_interval_sec, 1 if gex_attempted else 0)
        if gex_last_ok:
            gex_last_str = fmt_datetime_ms_utc8(gex_last_ok)
        else:
            gex_last_str = "-"
        gex_detail = "上次成功 {} | 累计 {}/{} | 新鲜度 {}".format(
            gex_last_str, gex_total_ok, gex_total_att, gex_freshness)

        row_gex = ["GEX 采集", gex_freq_cell, gex_detail]

        # ── 行 4: Backlog / 数据完整性 ────────────────────────────
        try:
            is_backlogged = bar_asm.is_backlogged()
            is_degraded   = bar_asm.is_cvd_degraded()
            metrics = bar_asm.get_last_cycle_metrics()
            hit_wall_time = metrics.get("hit_wall_time", False)
            wall_time_ms  = metrics.get("wall_time_ms", 0)
        except Exception:
            is_backlogged = False
            is_degraded   = False
            hit_wall_time = False
            wall_time_ms  = 0

        if is_degraded and is_backlogged:
            backlog_cell = "CVD 降级 + 积压"
        elif is_degraded:
            backlog_cell = "CVD 降级"
        elif is_backlogged:
            backlog_cell = "积压"
        else:
            backlog_cell = "正常"

        backlog_detail_parts = []
        if is_degraded:
            backlog_detail_parts.append("trade_id gap")
        if is_backlogged:
            backlog_detail_parts.append("旧成交未消化完")
        if hit_wall_time:
            backlog_detail_parts.append("drain 超时({}ms)".format(wall_time_ms))
        if not backlog_detail_parts:
            backlog_detail_parts.append("连续且已追上 ({}ms)".format(wall_time_ms))
        backlog_detail = " | ".join(backlog_detail_parts)

        row_backlog = ["Backlog / 完整性", backlog_cell, backlog_detail]

        # ── 行 5: 运行轮次 ────────────────────────────────────────
        try:
            total_bars = bar_asm.bar_count()
        except Exception:
            total_bars = 0
        current_bar_index = raw_safe.get("bar_index", 0)

        cycle_cell   = "#{}".format(cycle_index)
        cycle_detail = "总柱={} | 当前 bar_index={}".format(total_bars, current_bar_index)

        row_cycle = ["运行轮次", cycle_cell, cycle_detail]

        return {
            "type":  "table",
            "title": "数据采集",
            "cols":  ["指标", "当前值", "说明"],
            "rows":  [row_bar_build, row_agg, row_gex, row_backlog, row_cycle],
        }

    def _build_event_stream_table(self, snap_rec):
        """
        v1.2: 构造"高价值事件流"状态栏表。
        字段设计 (交易员观测直觉):
          时间      : UTC+8 HH:MM:SS，便于和钟表对齐
          Bar       : 事件发生的 Bar 序号
          类型      : 事件的中文标签
          细节      : 紧凑的关键参数 (方向/magnitude/分类+置信度)
        """
        n = CONFIG.get("event_stream_display_size", 5)
        try:
            recent = snap_rec.get_recent_events(n=n)
        except Exception:
            recent = []

        # 最新的在上
        recent_reversed = list(reversed(recent))

        rows = []
        for event in recent_reversed:
            time_str = fmt_timestamp_ms(event.get("ts_ms"))
            bar_idx  = str(event.get("bar_index", "-"))
            type_zh  = self.EVENT_TYPE_ZH.get(
                event.get("event_type"), event.get("event_type", "-"))
            detail_str = self._format_event_detail(event)
            rows.append([time_str, bar_idx, type_zh, detail_str])

        # 无事件时显示占位行
        if not rows:
            rows.append(["-", "-", "暂无事件", "-"])

        return {
            "type":  "table",
            "title": "高价值事件流（最近 {} 条）".format(len(recent_reversed) or 0),
            "cols":  ["时间", "Bar", "类型", "细节"],
            "rows":  rows,
        }

    def _format_event_detail(self, event):
        """
        v1.2: 事件 detail 字段的紧凑格式化。
        脱离确认: 方向 + σ 值
        缺口回补: 分类 + 置信度
        锚迁移:   Δ=位移倍数
        """
        etype  = event.get("event_type")
        detail = event.get("detail") or {}

        if etype == "departure_confirmed":
            nd = detail.get("normalized_deviation")
            if nd is None:
                return "-"
            direction = "上方" if nd > 0 else "下方"
            return "{} {}σ".format(direction, fmt_number(abs(nd), 2))

        if etype == "gap_closure_confirmed":
            classification = detail.get("classification_at_closure") or "-"
            conf = detail.get("confidence_at_closure") or "-"
            return "{} ({})".format(classification, conf)

        if etype == "anchor_shift":
            mag = detail.get("shift_magnitude")
            if mag is None:
                return "-"
            return "Δ={}".format(fmt_number(mag, 2))

        return "-"

    def _maybe_emit_state_log(self, labels, system_state):
        """去重的状态变化日志。"""
        labels_safe = labels or {}
        key = (
            system_state.get("event_state"),
            labels_safe.get("classification_result"),
            system_state.get("anchor_state"),
        )
        if key != self._last_state_log_key:
            self._last_state_log_key = key
            log_info(
                "状态: 脱离={} 分类={} ({}) 锚={}".format(
                    system_state.get("event_state", "-"),
                    labels_safe.get("classification_result", "-"),
                    labels_safe.get("confidence", "-"),
                    labels_safe.get("anchor_validity", "-"),
                )
            )


# ================================================================
# SECTION 10: MAIN
# ================================================================

def run():
    """
    v1.3.3 主策略循环 —— 显式的两类任务调度器。

    每轮主循环 (loop_sleep_sec):
      [采集阶段]
        1. anchor_ctx.clear_cycle_flag()   — 清零"本轮 GEX fetch 触发"标志
        2. anchor_ctx.check_update()       — GEX 轮询 (内部节流至 60s)
        3. bar_asm.poll_with_drain()       — aggTrades 受控 drain

      [事件驱动流水线 — 仅对新完成 Bar 执行]
        for bar in new_bars:
          Step A: DeviationTracker.update
          Step B: SystemStateManager.update
          Step C: ClassificationEvidence.compute
          Step D: LabelGenerator.generate
          Step E: SnapshotRecorder.write
          Step F: 事件日志 (departure_confirmed / gap_closure)
          Step G: display.on_bar —— 仅更新缓存, 打 Flag, 状态变化日志

      [time-driven 调度 — 不依赖是否有新 Bar]
        display.tick_chart()       — 每 chart_update_interval_sec
        display.tick_status()      — 每 status_update_interval_sec
        display.tick_logprofit()   — 每 logprofit_interval_sec
        display.tick_summary()     — 每 summary_log_interval_sec
        backlog / drain 摘要日志   — 节流 (v1.3.2 行为)

      4. cycle_index += 1
      5. time.sleep(loop_sleep_sec)

    v1.3.3 与 v1.3.2 的关键区别:
      v1.3.2 里 chart/status/summary/logprofit 都挂在 on_bar 下面,
      它们的真实频率 = "interval 过了" AND "刚好有新 Bar", 导致低流速
      段明明到了时间也不更新。v1.3.3 把这四个任务从 on_bar 中剥离,
      交给本循环的挂钟调度 —— 无论是否有新 Bar, 到了间隔就执行。
    """
    anchor_ctx    = AnchorContext()
    bar_asm       = BarAssembler()
    dev_tracker   = DeviationTracker()
    evidence_calc = ClassificationEvidence()
    state_mgr     = SystemStateManager()
    label_gen     = LabelGenerator()
    snap_rec      = SnapshotRecorder()
    display       = Display()
    display.init()

    log_info("Alpha Radar v1.3.3 启动。K={}, volume_bar_n={} BTC, "
             "drain_enabled={}, max_drain_rounds={}, max_drain_wall_time_ms={}".format(
                 CONFIG["K"], CONFIG["volume_bar_n"],
                 CONFIG["drain_enabled"],
                 CONFIG["max_drain_rounds"],
                 CONFIG["max_drain_wall_time_ms"]))
    log_info("v1.3.3 调度: time-driven[chart={}s status={}s logprofit={}s summary={}s] "
             "event-driven[on_bar: flags + state_log + cache]".format(
                 CONFIG["chart_update_interval_sec"],
                 CONFIG["status_update_interval_sec"],
                 CONFIG["logprofit_interval_sec"],
                 CONFIG["summary_log_interval_sec"]))

    # v1.3.2: backlog/drain 节流日志计时器 (run() 级别, 不迁入 Display)
    last_backlog_warn_sec  = 0
    last_drain_summary_sec = 0

    # v1.3.3: 主循环轮次计数器, 供状态栏"运行轮次"行使用
    cycle_index = 0

    while True:
        cycle_index += 1
        try:
            # ══════════════════════════════════════════════════════
            # 采集阶段
            # ══════════════════════════════════════════════════════

            # 清零本轮 GEX fetch 触发标志 (状态栏需要"本轮是否触发"0/1)
            anchor_ctx.clear_cycle_flag()

            # GEX 更新 (内部节流至 60s，每 tick 均调用)
            # drain 机制不干涉 AnchorContext 节奏（纪律: GEX 节奏独立）
            anchor_ctx.check_update()

            # 拉取新等币量柱 (v1.3.2: 受控 catch-up drain)
            new_bars = bar_asm.poll_with_drain()
            cycle_metrics = bar_asm.get_last_cycle_metrics()

            # ══════════════════════════════════════════════════════
            # 采集层节流日志 (v1.3.2 行为, run() 级别 time-driven)
            # ══════════════════════════════════════════════════════
            now_sec = int(time.time())

            # backlog 告警
            if cycle_metrics["backlogged"]:
                if now_sec - last_backlog_warn_sec >= CONFIG["backlog_warn_interval_sec"]:
                    last_backlog_warn_sec = now_sec
                    log_warn(
                        "[采集] backlog 未消化: drain_rounds={} wall={}ms "
                        "hit_limit={} hit_wall_time={} (市场流速 > REST 补给速率)".format(
                            cycle_metrics["catchup_rounds"],
                            cycle_metrics["wall_time_ms"],
                            cycle_metrics["hit_limit"],
                            cycle_metrics["hit_wall_time"]))

            # drain 周期摘要 (仅 drain 触发时才打)
            is_drain_noteworthy = (
                cycle_metrics["catchup_rounds"] > 1
                or cycle_metrics["wall_time_ms"] >= 500)
            if is_drain_noteworthy:
                if now_sec - last_drain_summary_sec >= CONFIG["drain_log_interval_sec"]:
                    last_drain_summary_sec = now_sec
                    log_info(
                        "[采集] drain 摘要: rounds={} trades={} bars={} wall={}ms".format(
                            cycle_metrics["catchup_rounds"],
                            cycle_metrics["trade_count"],
                            cycle_metrics["bar_count"],
                            cycle_metrics["wall_time_ms"]))

            # ══════════════════════════════════════════════════════
            # 事件驱动流水线: 仅对新完成的 Bar 执行
            # ══════════════════════════════════════════════════════
            for bar in new_bars:
                current_price = bar["close"]

                # 用 Volume Bar 序列的 σ_slow 计算 band_half（纪律1）
                std_usd   = bar_asm.get_slow_std_usd()
                band_half = anchor_ctx.compute_band_half(std_usd, current_price)
                flip_point = anchor_ctx.get_flip_point()

                # 检测锚偏移事件
                shift_event, shift_magnitude = anchor_ctx.detect_anchor_shift(
                    flip_point, band_half)

                anchor_freshness = anchor_ctx.get_freshness()

                # Step A: DeviationTracker
                dev_state = dev_tracker.update(bar, flip_point, band_half)

                # Step B: SystemStateManager (读 T-1 缓存)
                instructions = state_mgr.update(
                    anchor_freshness=anchor_freshness,
                    anchor_shift_event=shift_event,
                    shift_magnitude=shift_magnitude,
                    dev_state=dev_state,
                    prev_readiness=evidence_calc.get_readiness_cache(),
                )
                if instructions.get("reset_deviation_counter"):
                    dev_tracker.schedule_reset()

                # Step C: ClassificationEvidence
                raw_evidence = evidence_calc.compute(
                    bar, flip_point, band_half, dev_state, instructions,
                    cvd_degraded=bar_asm.is_cvd_degraded())

                # Step D: LabelGenerator
                labels = label_gen.generate(
                    raw_evidence, dev_state, state_mgr.get_state(),
                    anchor_source_ts_ms=anchor_ctx.get_source_ts_ms(),
                    anchor_stable_bar_count=state_mgr.get_anchor_stable_bar_count(),
                )

                # Step E: SnapshotRecorder
                band_clamped = anchor_ctx.was_band_clamped()
                snapshot = snap_rec.write(
                    bar, anchor_freshness, shift_event,
                    shift_magnitude, flip_point, band_half, band_clamped,
                    dev_state, raw_evidence, labels,
                    state_mgr.get_state(), instructions,
                    collection_metrics=cycle_metrics,
                )

                # Step F: 事件日志 (event-driven by definition)
                if dev_state.get("gap_closure_event"):
                    log_info(
                        "[事件] 缺口回补确认: bar={} 分类={} 置信={}".format(
                            bar["bar_index"],
                            labels.get("classification_result", "-"),
                            labels.get("confidence", "-"),
                        )
                    )
                if dev_state.get("departure_confirmed_event"):
                    log_info(
                        "[事件] 脱离确认: bar={} 偏差={}σ 方向={}".format(
                            bar["bar_index"],
                            fmt_number(dev_state.get("normalized_deviation"), 2),
                            "上方" if (dev_state.get("normalized_deviation") or 0) > 0 else "下方",
                        )
                    )

                # Step G: Display.on_bar —— 仅更新 latest 缓存 + 入队 chart row
                #                            + 打 Flag + 状态变化日志
                # v1.3.3: 本调用不再触发 chart/status/summary/logprofit 输出
                display.on_bar(
                    snapshot, anchor_ctx, bar_asm,
                    dev_state, labels, state_mgr.get_state(),
                    snap_rec)

            # ══════════════════════════════════════════════════════
            # time-driven 调度 (v1.3.3 核心改动)
            # 这四个 tick 各自内部节流, 无论本轮是否有新 Bar 都会被调用。
            # tick 内部若尚未到 interval 或缓存未就绪, 会优雅跳过 (不伪造)。
            # ══════════════════════════════════════════════════════
            display.tick_chart(anchor_ctx, bar_asm)
            display.tick_status(anchor_ctx, bar_asm, snap_rec, cycle_index)
            display.tick_logprofit()
            display.tick_summary()

        except Exception as error:
            log_error("run() exception: " + str(error))
            log_error(traceback.format_exc())

        time.sleep(CONFIG["loop_sleep_sec"])


def main():
    """fmz 策略入口。"""
    try:
        run()
    except Exception as error:
        log_error("main() exception: " + str(error))
        log_error(traceback.format_exc())


if __name__ == "__main__":
    main()
