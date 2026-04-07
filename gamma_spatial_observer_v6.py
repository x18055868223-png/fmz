# -*- coding: utf-8 -*-
"""
Gamma Spatial Observer v6  (FMZ single-file)
================================================================
Alpha Radar — 微观信号验证与观测框架。无任何实盘发单逻辑。

Layer1 Anchor : GexMonitor -> flip_point(=吸波轴) + spring + pressure_entry
Layer2 Engine : Binance aggTrades -> Volume Bars -> OLS + std
Layer3 Observer: BandFSM + MarkerTracker + SpatialTracker -> Scenario

吸波带物理模型 v6 (流体自适应):
  axis = flip_point
  sigma_count = base(3) + bonus(tanh(spring_capacity_per_sigma / 5)) * max_bonus(3)
    spring_capacity_per_sigma = spring * std_usd / volume_bar_threshold
    弱弹簧(无吸收力) → sigma_count ≈ 3
    强弹簧(强吸收力) → sigma_count → 6 (自然饱和)
  band_half = std_slow_usd * sigma_count
  无硬编码 clamp。sigma_count 通过 tanh 自然饱和。

压力入口 (v6):
  不用 pressure_node * 0.998 魔法数字。
  通过 hedging_curve 插值，找到 |hedging_btc| 首次超过
  spring_threshold(= spring × grid) 的价格，作为动态压力入口。

实时事件追踪:
  MarkerTracker 暴露 get_live_health() → {dev_expansion, momentum_change}
  classify_scenario 实时消费健康度数据
  图表在高阶场景变化时追打 Flag
================================================================
"""

import json
import time
import math
import os
import ssl
import datetime
import traceback
import urllib.request
import urllib.error
from urllib.parse import urlencode


# ================================================================
# CONFIG
# ================================================================
CONFIG = {
    # --- Layer1: Anchor ---
    "asset": "BTC",
    "exchange": "all",
    "lite": "true",
    "gex_base_url": "https://gexmonitor.com/api/gex-latest",
    "http_timeout_sec": 5,
    "http_max_retries": 2,
    "http_retry_delays_sec": [0.6, 1.2],
    "freshness_threshold_ms": 180_000,
    "min_fetch_interval_ms": 60_000,
    "axis_snapshot_path": "/home/bitnami/logs/storage/654434/gex_axis_snapshot.json",
    "request_meta_path": "/home/bitnami/logs/storage/654434/gex_axis_request_meta.json",
    "pressure_node_min_hedging_btc": -1000.0,
    "pressure_coeff_cap": 3.0,
    "axis_dirty_reject_pct": 2.5,
    "axis_dirty_recover_pct": 1.0,

    # --- Layer2: Engine ---
    "binance_spot_base_url": "https://api.binance.com/api/v3/aggTrades",
    "binance_spot_symbol": "BTCUSDT",
    "agg_trades_limit": 1000,
    "volume_bar_threshold_btc": 10.0,
    "fast_window": 5,
    "slow_window": 30,

    # --- Layer3: Observer ---
    "positive_deviation_threshold": 0.003,
    "spatial_window_hours": 4.0,
    "momentum_min_norm_bps": 0.8,
    "momentum_min_cvd_ratio": 0.03,
    "b_strength_multiplier": 1.5,
    "marker_confirm_rounds": 2,
    "marker_expire_rounds": 2,

    # --- Absorption Band (流体自适应, 无硬编码 clamp) ---
    "band_base_sigma": 3.0,
    "band_max_sigma_bonus": 3.0,
    "band_spring_capacity_midpoint": 5.0,
    "band_fallback_half_pct": 0.005,
    "gravity_lost_pct": 0.03,
    "band_break_confirm_ticks": 2,
    "band_reenter_confirm_ticks": 3,

    # --- Runtime ---
    "loop_sleep_sec": 2,
    "summary_log_interval_sec": 60,
    "logprofit_min_interval_sec": 10,
    "chart_update_interval_sec": 10,
    "exchange_price_precision": 2,
    "exchange_amount_precision": 4,
}


# ================================================================
# Utility Functions
# ================================================================
def now_ms():
    return int(time.time() * 1000)


def safe_float(x):
    """安全转换为 float，无效值返回 None"""
    try:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def safe_int(x):
    """安全转换为 int，无效值返回 None"""
    try:
        if x is None or isinstance(x, bool):
            return None
        if isinstance(x, (int, float, str)):
            return int(float(x))
        return None
    except Exception:
        return None


def parse_iso_to_ms(text):
    """解析 ISO 8601 时间字符串为毫秒时间戳"""
    if not text:
        return None
    try:
        s = str(text).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _get_fmz_function(name):
    """获取 FMZ 平台内置函数，不存在则返回 None"""
    fn = globals().get(name)
    return fn if callable(fn) else None


def log_info(msg):
    fn = _get_fmz_function("Log")
    if fn:
        fn(str(msg))
    else:
        print(str(msg))


def log_warn(msg):
    log_info("[WARN] " + str(msg))


def log_error(msg):
    log_info("[ERROR] " + str(msg))


def log_status(text, tables=None):
    """输出 FMZ 状态栏内容"""
    try:
        payload = str(text)
        if tables:
            payload += "\n`" + json.dumps(tables, ensure_ascii=False) + "`"
        fn = _get_fmz_function("LogStatus")
        if fn:
            fn(payload)
        else:
            print(payload)
    except Exception as e:
        log_warn("status render fail: " + str(e))


def fmt_price(x):
    """格式化价格，保留2位小数"""
    v = safe_float(x)
    if v is None:
        return "-"
    return "{:.2f}".format(v)


def fmt_number(x, decimals=2):
    """格式化数值，指定小数位"""
    v = safe_float(x)
    if v is None:
        return "-"
    return ("{:." + str(decimals) + "f}").format(v)


def fmt_percent(x):
    """格式化百分比，带符号"""
    v = safe_float(x)
    if v is None:
        return "-"
    return "{:+.3f}%".format(v * 100.0)


def fmt_yes_no(v):
    return "是" if v else "否"


def fmt_timestamp(ms):
    """毫秒时间戳转 UTC+8 时间字符串"""
    t = safe_int(ms)
    if t is None:
        return "-"
    try:
        dt = datetime.datetime.utcfromtimestamp(t / 1000.0) + datetime.timedelta(hours=8)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return "-"


def compute_deviation_pct(price, reference):
    """计算价格偏离百分比"""
    p = safe_float(price)
    r = safe_float(reference)
    if p is None or r is None or r == 0:
        return None
    return p / r - 1.0


def persistent_read(key, default=None):
    """FMZ _G() 安全读取，带默认值降级"""
    fn = _get_fmz_function("_G")
    if fn:
        try:
            v = fn(key)
            if v is not None:
                return v
        except Exception:
            pass
    return default


def persistent_write(key, value):
    """FMZ _G() 安全写入"""
    fn = _get_fmz_function("_G")
    if fn:
        try:
            fn(key, value)
        except Exception:
            pass


# ================================================================
# HTTP Utility
# ================================================================
def http_get_json(url, timeout=5, retries=2):
    """带重试的 HTTP GET 请求，返回 (dict, error_string)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0",
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
        "Referer": "https://gexmonitor.com/",
        "Origin": "https://gexmonitor.com",
    }
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    delays = CONFIG.get("http_retry_delays_sec", [0.6, 1.2])
    last_err = None

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url=url, headers=headers, method="GET")
            resp = opener.open(req, timeout=timeout)
            status_code = safe_int(getattr(resp, "status", None))
            if status_code != 200:
                raise RuntimeError("status_not_200")
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                return payload, None
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(delays[min(attempt, len(delays) - 1)])

    return None, last_err


# ================================================================
# Layer1: Anchor
# ================================================================
class Anchor:
    """
    从 GexMonitor 提取空间锚点:
      1. flip_point — Gamma 中轴 (做市商 delta 对冲翻转价, 同时作为吸波轴)
      2. spring — 翻转区弹簧系数 |dH/dP|
      3. grid — hedging_curve 节点间距 (做市商控制网格)
      4. pressure_node — 上方首个显著负 hedging 节点
    含 dirty gate 稳压、snapshot fallback、pressure 二次确认。
    """

    def __init__(self):
        self._last_live = None
        self._dirty_hold = False
        self._pending_pressure = {"node": None, "hits": 0}
        self._last_fetch_attempt_ms = 0
        self._last_fetch_success_ms = 0
        self._hedging_curve = []
        self._load_request_meta()

    # ---- Persistence ----

    def _load_request_meta(self):
        """从磁盘加载上次请求时间戳"""
        try:
            path = CONFIG.get("request_meta_path")
            if path and os.path.exists(path):
                with open(path, "r") as f:
                    obj = json.load(f)
                self._last_fetch_attempt_ms = safe_int(obj.get("lfa")) or 0
                self._last_fetch_success_ms = safe_int(obj.get("lfs")) or 0
        except Exception:
            pass

    def _save_request_meta(self):
        """持久化请求时间戳到磁盘"""
        try:
            path = CONFIG.get("request_meta_path")
            if not path:
                return
            folder = os.path.dirname(path)
            if folder and not os.path.exists(folder):
                os.makedirs(folder)
            with open(path, "w") as f:
                json.dump({
                    "lfa": self._last_fetch_attempt_ms,
                    "lfs": self._last_fetch_success_ms,
                }, f)
        except Exception:
            pass

    def _load_axis_snapshot(self):
        """从磁盘加载轴心快照"""
        try:
            path = CONFIG.get("axis_snapshot_path")
            if not path or not os.path.exists(path):
                return None
            with open(path, "r") as f:
                obj = json.load(f)
            flip_point = safe_float(obj.get("fp"))
            source_ts = safe_int(obj.get("ts"))
            if flip_point and flip_point > 0 and source_ts:
                return obj
        except Exception:
            pass
        return None

    def _save_axis_snapshot(self, snapshot_data):
        """持久化轴心快照到磁盘"""
        try:
            path = CONFIG.get("axis_snapshot_path")
            if not path:
                return
            folder = os.path.dirname(path)
            if folder and not os.path.exists(folder):
                os.makedirs(folder)
            with open(path, "w") as f:
                json.dump(snapshot_data, f, ensure_ascii=False)
        except Exception:
            pass

    # ---- GEX Payload Parsing ----

    def _parse_payload(self, payload):
        """从 GexMonitor JSON 中提取全部锚点数据"""
        if not isinstance(payload, dict):
            return None

        flip_point = safe_float(payload.get("flip_point"))
        if not flip_point or flip_point <= 0:
            return None

        # 时间戳解析 (多路径降级)
        source_ts_ms = None
        ts_raw = payload.get("timestamp")
        if ts_raw:
            if isinstance(ts_raw, (int, float)) and ts_raw > 1e12:
                source_ts_ms = safe_int(ts_raw)
            else:
                source_ts_ms = parse_iso_to_ms(ts_raw)
        if source_ts_ms is None:
            try:
                update_time = payload["profiles"]["total"]["meta"]["updateTime"]
                source_ts_ms = parse_iso_to_ms(update_time)
            except (KeyError, TypeError):
                pass
        if source_ts_ms is None:
            return None

        asset_price = safe_float(payload.get("asset_price"))
        hedging_flows = payload.get("hedging_flows", {})
        hedging_curve = hedging_flows.get("hedging_curve", [])

        # 压力候选节点: flip 上方的显著负 hedging 节点
        min_hedging = safe_float(CONFIG.get("pressure_node_min_hedging_btc")) or -1000.0
        pressure_candidates = []
        for item in (hedging_curve if isinstance(hedging_curve, list) else []):
            node_price = safe_float(item.get("price"))
            node_hedging = safe_float(item.get("hedging_btc"))
            if node_price and node_hedging and node_price > flip_point and node_hedging < 0 and node_hedging <= min_hedging:
                pressure_candidates.append({"price": node_price, "hedging_btc": node_hedging})
        pressure_candidates.sort(key=lambda x: x["price"])

        # 对冲阶梯: ±5% 范围内的 hedging_curve 节点
        ladder_rows = []
        for item in (hedging_curve if isinstance(hedging_curve, list) else []):
            move_pct = safe_int(item.get("move_pct"))
            node_price = safe_float(item.get("price"))
            node_hedging = safe_float(item.get("hedging_btc"))
            if move_pct is not None and node_price and node_hedging is not None and -5 <= move_pct <= 5:
                ladder_rows.append({"move_pct": move_pct, "price": node_price, "hedging_btc": node_hedging})
        ladder_rows.sort(key=lambda x: x["move_pct"])

        # 弹簧系数和动态压力入口
        spring_constant, dynamic_pressure_entry = self._extract_spring_and_pressure_entry(hedging_curve, flip_point)

        return {
            "flip_point": flip_point,
            "asset_price": asset_price,
            "source_ts_ms": source_ts_ms,
            "candidates": pressure_candidates,
            "ladder": ladder_rows,
            "spring": spring_constant,
            "dynamic_pressure_entry": dynamic_pressure_entry,
            "hedging_curve": hedging_curve,
        }

    def _extract_spring_and_pressure_entry(self, curve, flip_point):
        """
        从 hedging_curve 提取:
          spring = |dH/dP| at flip (做市商在翻转区的对冲强度, BTC/USD)
          pressure_entry = |hedging_btc| 首次超过 spring_threshold 的插值价格
            (做市商卖压累积超过单网格弹簧力的位置 = 真实压力入口)

        不再使用 grid 作为带宽约束。grid 只是 API 采样分辨率, 无物理意义。
        """
        fp = safe_float(flip_point)
        if not fp:
            return 0.0, None

        # 解析并排序节点
        nodes = []
        for item in (curve if isinstance(curve, list) else []):
            node_price = safe_float(item.get("price"))
            node_hedging = safe_float(item.get("hedging_btc"))
            if node_price is not None and node_hedging is not None:
                nodes.append((node_price, node_hedging))
        nodes.sort(key=lambda x: x[0])

        if len(nodes) < 2:
            return 0.0, None

        # === Spring: |dH/dP| at segment containing flip ===
        spring_constant = 0.0
        for i in range(1, len(nodes)):
            if nodes[i - 1][0] <= fp <= nodes[i][0]:
                delta_price = nodes[i][0] - nodes[i - 1][0]
                if delta_price > 0:
                    spring_constant = abs(nodes[i][1] - nodes[i - 1][1]) / delta_price
                break

        # 兜底: 如果 flip 不在任何段内, 用最近的两个节点估算
        if spring_constant == 0.0:
            closest_nodes = sorted(nodes, key=lambda n: abs(n[0] - fp))[:2]
            if len(closest_nodes) == 2:
                delta_price = abs(closest_nodes[1][0] - closest_nodes[0][0])
                if delta_price > 0:
                    spring_constant = abs(closest_nodes[1][1] - closest_nodes[0][1]) / delta_price

        # === Dynamic pressure entry ===
        # spring_threshold = spring * average_grid_step
        # 这是"做市商在一个网格步长上产生的对冲量"
        # 当某价位的 |hedging_btc| 超过此值, 卖压已超出弹簧吸收能力
        avg_grid = 0.0
        grid_count = 0
        for i in range(1, len(nodes)):
            mid = (nodes[i][0] + nodes[i - 1][0]) / 2
            if abs(mid - fp) / fp < 0.10:
                interval = nodes[i][0] - nodes[i - 1][0]
                if interval > 0:
                    avg_grid += interval
                    grid_count += 1
        if grid_count > 0:
            avg_grid = avg_grid / grid_count
        else:
            avg_grid = fp * 0.01

        spring_threshold = spring_constant * avg_grid if spring_constant > 0 else 500.0
        # 至少 200 BTC, 防止极弱弹簧时阈值过低
        spring_threshold = max(spring_threshold, 200.0)

        # 在 flip 上方找 |hedging| 首次超过 spring_threshold 的插值价格
        dynamic_pressure_entry = None
        for i in range(1, len(nodes)):
            price_lo, hedging_lo = nodes[i - 1]
            price_hi, hedging_hi = nodes[i]
            if price_lo <= fp:
                continue
            # hedging_lo 还没超阈值, hedging_hi 超了 -> 插值
            if abs(hedging_lo) <= spring_threshold and abs(hedging_hi) > spring_threshold:
                if hedging_hi != hedging_lo:
                    # 插值: 找到 |h| == spring_threshold 的价格
                    # h(p) = h_lo + (h_hi - h_lo) * (p - p_lo) / (p_hi - p_lo)
                    # 由于 hedging 为负 (上方卖压), 找 h = -spring_threshold
                    target_h = -spring_threshold
                    ratio = (target_h - hedging_lo) / (hedging_hi - hedging_lo)
                    ratio = max(0.0, min(1.0, ratio))
                    dynamic_pressure_entry = price_lo + ratio * (price_hi - price_lo)
                else:
                    dynamic_pressure_entry = price_lo
                break

        # 兜底: 如果插值失败, 用第一个 |h| > threshold 的节点价格
        if dynamic_pressure_entry is None:
            for price, hedging in nodes:
                if price > fp and hedging < 0 and abs(hedging) > spring_threshold:
                    dynamic_pressure_entry = price
                    break

        return round(spring_constant, 6), round(dynamic_pressure_entry, 2) if dynamic_pressure_entry else None

    # ---- Dirty Gate (脏数据过滤) ----

    def _assess_axis_dirty(self, parsed, baseline):
        """
        判断新 live 数据是否为脏数据跳变。
        用现货价格变化锚定: 轴线变化 - 现货变化 = 净无理偏移。
        返回: (is_dirty, can_recover, net_shift_pct)
        """
        if not baseline:
            return False, False, None

        old_flip = safe_float(baseline.get("flip_point") or baseline.get("fp"))
        new_flip = safe_float(parsed.get("flip_point"))
        old_asset = safe_float(baseline.get("asset_price") or baseline.get("ap"))
        new_asset = safe_float(parsed.get("asset_price"))

        if not old_flip or not new_flip or old_flip <= 0:
            return False, False, None

        axis_diff_pct = abs(new_flip - old_flip) / old_flip * 100.0
        spot_diff_pct = 0.0
        if old_asset and old_asset > 0 and new_asset:
            spot_diff_pct = abs(new_asset - old_asset) / old_asset * 100.0

        net_irrational_shift = max(0.0, axis_diff_pct - spot_diff_pct)

        reject_threshold = safe_float(CONFIG.get("axis_dirty_reject_pct")) or 2.5
        recover_threshold = safe_float(CONFIG.get("axis_dirty_recover_pct")) or 1.0

        is_dirty = net_irrational_shift >= reject_threshold
        can_recover = net_irrational_shift < recover_threshold

        return is_dirty, can_recover, net_irrational_shift

    # ---- Pressure Node Selection (二次确认) ----

    def _select_stable_pressure(self, candidates, baseline):
        """
        稳压压力节点选择: 新节点需要连续出现 2 次才切换。
        """
        old_node = safe_float(baseline.get("pressure_node") or baseline.get("pn")) if baseline else None
        old_hedging = safe_float(baseline.get("pressure_hedging_btc") or baseline.get("ph")) if baseline else None

        # 旧节点仍在候选中 -> 保持
        if old_node:
            for candidate in candidates:
                if abs(safe_float(candidate["price"]) - old_node) < 1e-6:
                    self._pending_pressure = {"node": None, "hits": 0}
                    return old_node, safe_float(candidate["hedging_btc"])

        if not candidates:
            self._pending_pressure = {"node": None, "hits": 0}
            return old_node, old_hedging

        new_node_price = safe_float(candidates[0]["price"])
        new_node_hedging = safe_float(candidates[0]["hedging_btc"])
        pending_node = safe_float(self._pending_pressure.get("node"))

        if pending_node and new_node_price and abs(pending_node - new_node_price) < 1e-6:
            self._pending_pressure["hits"] += 1
        else:
            self._pending_pressure = {"node": new_node_price, "hits": 1}

        if self._pending_pressure["hits"] >= 2:
            self._pending_pressure = {"node": None, "hits": 0}
            return new_node_price, new_node_hedging

        return old_node, old_hedging

    # ---- Main Update ----

    def update(self):
        """每轮调用, 返回完整的 anchor_state dict"""
        current_ms = now_ms()
        should_fetch = (current_ms - self._last_fetch_attempt_ms) >= CONFIG.get("min_fetch_interval_ms", 60000)

        if should_fetch:
            self._last_fetch_attempt_ms = current_ms
            self._save_request_meta()

            url = CONFIG["gex_base_url"] + "?" + urlencode({
                "asset": CONFIG["asset"],
                "exchange": CONFIG["exchange"],
                "lite": CONFIG["lite"],
                "t": current_ms,
            })
            payload, fetch_error = http_get_json(url, CONFIG["http_timeout_sec"], CONFIG["http_max_retries"])

            if payload:
                parsed = self._parse_payload(payload)
                if parsed:
                    age_ms = current_ms - parsed["source_ts_ms"]
                    is_fresh = age_ms <= CONFIG["freshness_threshold_ms"]

                    baseline = self._last_live or self._load_axis_snapshot()
                    is_dirty, can_recover, net_shift = self._assess_axis_dirty(parsed, baseline)

                    # 脏数据门控状态机
                    if is_fresh:
                        if self._dirty_hold:
                            if can_recover:
                                self._dirty_hold = False
                            else:
                                is_dirty = True
                        elif is_dirty:
                            self._dirty_hold = True

                    if is_dirty:
                        self._pending_pressure = {"node": None, "hits": 0}
                        if self._last_live:
                            return self._reuse_last_live("dirty")
                        snapshot = self._load_axis_snapshot()
                        if snapshot:
                            return self._build_from_snapshot(snapshot, "dirty")
                        return self._build_unavailable("dirty")

                    # 稳压压力节点选择
                    if is_fresh:
                        pressure_node, pressure_hedging = self._select_stable_pressure(parsed["candidates"], baseline)
                    else:
                        self._pending_pressure = {"node": None, "hits": 0}
                        pressure_node = safe_float(baseline.get("pressure_node") or baseline.get("pn")) if baseline else None
                        pressure_hedging = safe_float(baseline.get("pressure_hedging_btc") or baseline.get("ph")) if baseline else None

                    # 使用动态压力入口 (从 hedging gradient 推导, 替代旧的 node * 0.998)
                    pressure_entry = parsed.get("dynamic_pressure_entry")
                    if pressure_entry is None and pressure_node:
                        # 降级: 动态推导失败时, 使用节点价格减去 1 个 grid step 作为缓冲
                        # grid step 从 hedging_curve 的平均间距推导 (非魔法数字)
                        nodes_sorted = sorted(
                            [(safe_float(item.get("price")), safe_float(item.get("hedging_btc")))
                             for item in parsed.get("hedging_curve", [])
                             if safe_float(item.get("price")) is not None],
                            key=lambda x: x[0]
                        )
                        if len(nodes_sorted) >= 2:
                            avg_step = (nodes_sorted[-1][0] - nodes_sorted[0][0]) / (len(nodes_sorted) - 1)
                            pressure_entry = pressure_node - avg_step
                        else:
                            pressure_entry = pressure_node * 0.99  # 最终降级: 1% 缓冲

                    self._hedging_curve = parsed.get("hedging_curve", [])

                    state = {
                        "flip_point": parsed["flip_point"],
                        "source_ts_ms": parsed["source_ts_ms"],
                        "asset_price": parsed["asset_price"],
                        "pressure_node": pressure_node,
                        "pressure_entry": pressure_entry,
                        "pressure_hedging_btc": pressure_hedging,
                        "ladder": parsed["ladder"],
                        "spring": parsed["spring"],
                        "usable": True,
                        "fresh": is_fresh,
                        "mode": "live",
                        "status": "fresh" if is_fresh else "stale",
                        "error": None,
                    }

                    self._last_live = dict(state)
                    self._save_axis_snapshot({
                        "fp": state["flip_point"],
                        "ap": state["asset_price"],
                        "ts": state["source_ts_ms"],
                        "pn": pressure_node,
                        "pe": pressure_entry,
                        "ph": pressure_hedging,
                    })
                    self._last_fetch_success_ms = current_ms
                    self._save_request_meta()
                    return state

            # fetch 失败或 parse 失败
            self._pending_pressure = {"node": None, "hits": 0}

        # 非 fetch 轮次: 复用缓存
        if self._last_live:
            return self._reuse_last_live("reuse")
        snapshot = self._load_axis_snapshot()
        if snapshot:
            return self._build_from_snapshot(snapshot, "fallback")
        return self._build_unavailable("no_data")

    def _reuse_last_live(self, error_tag):
        """复用上一次 live 数据，更新时效性字段"""
        out = dict(self._last_live)
        age_ms = now_ms() - (safe_int(out.get("source_ts_ms")) or 0)
        out["fresh"] = age_ms <= CONFIG["freshness_threshold_ms"]
        out["usable"] = age_ms <= 70 * 60 * 1000
        out["status"] = "fresh" if out["fresh"] else "stale"
        out["error"] = error_tag
        return out

    def _build_from_snapshot(self, snapshot, error_tag):
        """从磁盘快照构建 anchor_state"""
        flip_point = safe_float(snapshot.get("fp"))
        source_ts = safe_int(snapshot.get("ts"))
        age_ms = now_ms() - source_ts if source_ts else 999999999
        pressure_node = safe_float(snapshot.get("pn"))

        pressure_entry = safe_float(snapshot.get("pe"))
        if not pressure_entry and pressure_node:
            pressure_entry = pressure_node * 0.998  # snapshot 降级

        return {
            "flip_point": flip_point,
            "source_ts_ms": source_ts,
            "asset_price": safe_float(snapshot.get("ap")),
            "pressure_node": pressure_node,
            "pressure_entry": pressure_entry,
            "pressure_hedging_btc": safe_float(snapshot.get("ph")),
            "ladder": [],
            "spring": 0.0,
            "usable": flip_point is not None and flip_point > 0 and age_ms <= 70 * 60 * 1000,
            "fresh": age_ms <= CONFIG["freshness_threshold_ms"],
            "mode": "snapshot",
            "status": "stale",
            "error": error_tag,
        }

    def _build_unavailable(self, reason):
        """构建不可用状态"""
        return {
            "flip_point": None,
            "source_ts_ms": None,
            "asset_price": None,
            "pressure_node": None,
            "pressure_entry": None,
            "pressure_hedging_btc": None,
            "ladder": [],
            "spring": 0.0,
            "usable": False,
            "fresh": False,
            "mode": "unavailable",
            "status": "unavailable",
            "error": reason,
        }

    def interpolate_hedging(self, target_price):
        """在 hedging_curve 节点间线性插值，返回做市商在该价位的净对冲量(BTC)"""
        tp = safe_float(target_price)
        if tp is None or not self._hedging_curve:
            return None

        nodes = []
        for item in self._hedging_curve:
            node_price = safe_float(item.get("price"))
            node_hedging = safe_float(item.get("hedging_btc"))
            if node_price is not None and node_hedging is not None:
                nodes.append((node_price, node_hedging))
        nodes.sort(key=lambda x: x[0])

        if not nodes or tp < nodes[0][0] or tp > nodes[-1][0]:
            return None

        for i in range(1, len(nodes)):
            if nodes[i][0] >= tp:
                price_lo, hedging_lo = nodes[i - 1]
                price_hi, hedging_hi = nodes[i]
                if price_hi != price_lo:
                    ratio = (tp - price_lo) / (price_hi - price_lo)
                else:
                    ratio = 0
                return hedging_lo + ratio * (hedging_hi - hedging_lo)

        return None


# ================================================================
# Layer2: Engine
# ================================================================
def ols_slope(values):
    """OLS 线性回归斜率 (最小二乘法)"""
    if not isinstance(values, list) or len(values) < 2:
        return None
    ys = [safe_float(v) for v in values]
    if any(v is None for v in ys):
        return None

    n = len(ys)
    mean_x = (n - 1) / 2.0
    mean_y = sum(ys) / n
    covariance = 0.0
    variance = 0.0

    for i in range(n):
        dx = i - mean_x
        covariance += dx * (ys[i] - mean_y)
        variance += dx * dx

    if variance == 0:
        return 0.0
    return covariance / variance


def std_dev(values):
    """总体标准差"""
    if not isinstance(values, list) or len(values) < 2:
        return None
    vs = [safe_float(v) for v in values]
    if any(v is None for v in vs):
        return None

    n = len(vs)
    mean = sum(vs) / n
    return math.sqrt(sum((v - mean) ** 2 for v in vs) / n)


class Engine:
    """
    Binance 现货 aggTrades -> 等币量柱 (Volume Bars) -> 双窗口 OLS 斜率 + 标准差

    输出字段:
      price               当前最新成交价
      pn_f / cn_f         快窗价格/CVD 归一化斜率 (bps/bar, ratio/bar)
      pn_s / cn_s         慢窗价格/CVD 归一化斜率
      std_f_bps           快窗价格标准差 (bps)
      std_s_bps           慢窗价格标准差 (bps)
      fast_ready          快窗是否就绪
      slow_ready          慢窗是否就绪
      bar_count           当前累计柱数
      poll_ok             本轮 REST 拉取是否成功
    """

    def __init__(self):
        self._last_trade_id = None
        self._last_trade_price = None
        self._current_bar_volume = 0.0
        self._current_bar_cvd_delta = 0.0
        self._current_bar_last_price = None
        self._cvd_cumulative = 0.0
        self._completed_bars = []

    def poll(self):
        """拉取最新成交数据并更新 volume bars"""
        try:
            raw_trades = self._fetch_agg_trades()
        except Exception as e:
            log_warn("Engine fetch: " + str(e))
            return self._build_state(poll_ok=False)

        parsed_trades = self._parse_trades(raw_trades)
        for trade in parsed_trades:
            self._last_trade_price = trade["price"]
            self._last_trade_id = trade["id"]
            self._ingest_trade(trade)

        return self._build_state(poll_ok=True)

    def _fetch_agg_trades(self):
        """从 Binance REST API 拉取 aggTrades"""
        params = {
            "symbol": CONFIG["binance_spot_symbol"],
            "limit": CONFIG["agg_trades_limit"],
        }
        if self._last_trade_id is not None:
            params["fromId"] = self._last_trade_id + 1

        url = CONFIG["binance_spot_base_url"] + "?" + urlencode(params)
        ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        req = urllib.request.Request(
            url=url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            method="GET",
        )
        resp = opener.open(req, timeout=CONFIG["http_timeout_sec"])
        data = json.loads(resp.read().decode("utf-8", errors="replace"))

        if not isinstance(data, list):
            raise RuntimeError("not_list")
        return data

    def _parse_trades(self, raw_rows):
        """解析 aggTrades 数组，检测数据间断"""
        parsed = []
        for item in raw_rows:
            if not isinstance(item, dict):
                continue

            trade_id = safe_int(item.get("a"))
            trade_price = safe_float(item.get("p"))
            trade_qty = safe_float(item.get("q"))
            is_buyer_maker = item.get("m")

            if (trade_id is None or trade_price is None or trade_qty is None
                    or trade_price <= 0 or trade_qty <= 0 or not isinstance(is_buyer_maker, bool)):
                continue

            # 数据间断检测
            if self._last_trade_id is not None and trade_id > self._last_trade_id + 1 and not parsed:
                self._reset_aggregation()
                log_warn("aggTrades gap detected, engine reset")

            signed_qty = trade_qty if not is_buyer_maker else -trade_qty
            parsed.append({
                "id": trade_id,
                "price": trade_price,
                "qty": trade_qty,
                "signed_qty": signed_qty,
            })

        return parsed

    def _reset_aggregation(self):
        """重置 volume bar 聚合状态"""
        self._current_bar_volume = 0.0
        self._current_bar_cvd_delta = 0.0
        self._current_bar_last_price = None
        self._cvd_cumulative = 0.0
        self._completed_bars = []

    def _ingest_trade(self, trade):
        """将单笔成交吸收到 volume bar 聚合器中"""
        bar_threshold = safe_float(CONFIG.get("volume_bar_threshold_btc")) or 10.0
        remaining_qty = trade["qty"]

        while remaining_qty > 0:
            space_in_bar = bar_threshold - self._current_bar_volume
            take_qty = min(remaining_qty, max(space_in_bar, bar_threshold))
            fill_ratio = take_qty / trade["qty"]

            self._current_bar_volume += take_qty
            self._current_bar_cvd_delta += trade["signed_qty"] * fill_ratio
            self._current_bar_last_price = trade["price"]
            remaining_qty -= take_qty

            if self._current_bar_volume >= bar_threshold:
                self._cvd_cumulative += self._current_bar_cvd_delta
                self._completed_bars.append({
                    "cp": self._current_bar_last_price,
                    "cvd": self._cvd_cumulative,
                })
                max_bars = CONFIG.get("slow_window", 30)
                if len(self._completed_bars) > max_bars:
                    self._completed_bars.pop(0)
                self._current_bar_volume = 0.0
                self._current_bar_cvd_delta = 0.0
                self._current_bar_last_price = None

    def _build_state(self, poll_ok):
        """从已完成的 volume bars 计算双窗口指标"""
        fast_n = CONFIG.get("fast_window", 5)
        slow_n = CONFIG.get("slow_window", 30)
        bar_count = len(self._completed_bars)
        fast_ready = bar_count >= fast_n
        slow_ready = bar_count >= slow_n
        current_price = self._last_trade_price
        volume_threshold = safe_float(CONFIG.get("volume_bar_threshold_btc")) or 10.0

        # 快窗指标
        price_slope_fast = None
        cvd_slope_fast = None
        std_fast = None
        if fast_ready:
            fast_bars = self._completed_bars[-fast_n:]
            fast_prices = [bar["cp"] for bar in fast_bars]
            price_slope_fast = ols_slope(fast_prices)
            cvd_slope_fast = ols_slope([bar["cvd"] for bar in fast_bars])
            std_fast = std_dev(fast_prices)

        # 慢窗指标
        price_slope_slow = None
        cvd_slope_slow = None
        std_slow = None
        if slow_ready:
            slow_bars = self._completed_bars[-slow_n:]
            slow_prices = [bar["cp"] for bar in slow_bars]
            price_slope_slow = ols_slope(slow_prices)
            cvd_slope_slow = ols_slope([bar["cvd"] for bar in slow_bars])
            std_slow = std_dev(slow_prices)

        # 归一化
        price_norm_fast = None
        if price_slope_fast is not None and current_price and current_price > 0:
            price_norm_fast = price_slope_fast / current_price * 10000.0

        cvd_norm_fast = None
        if cvd_slope_fast is not None and volume_threshold > 0:
            cvd_norm_fast = cvd_slope_fast / volume_threshold

        price_norm_slow = None
        if price_slope_slow is not None and current_price and current_price > 0:
            price_norm_slow = price_slope_slow / current_price * 10000.0

        cvd_norm_slow = None
        if cvd_slope_slow is not None and volume_threshold > 0:
            cvd_norm_slow = cvd_slope_slow / volume_threshold

        std_fast_bps = None
        if std_fast is not None and current_price and current_price > 0:
            std_fast_bps = std_fast / current_price * 10000.0

        std_slow_bps = None
        if std_slow is not None and current_price and current_price > 0:
            std_slow_bps = std_slow / current_price * 10000.0

        return {
            "price": current_price,
            "ps_f": price_slope_fast,
            "cs_f": cvd_slope_fast,
            "ps_s": price_slope_slow,
            "cs_s": cvd_slope_slow,
            "pn_f": price_norm_fast,
            "cn_f": cvd_norm_fast,
            "pn_s": price_norm_slow,
            "cn_s": cvd_norm_slow,
            "std_f_bps": std_fast_bps,
            "std_s_bps": std_slow_bps,
            "fast_ready": fast_ready,
            "slow_ready": slow_ready,
            "bar_count": bar_count,
            "poll_ok": poll_ok,
        }


# ================================================================
# Layer3: Observer
# ================================================================

# --- 吸波带动态计算 ---

def compute_absorption_band(anchor, engine):
    """
    流体吸波带: axis ± (sigma_count * std_slow_usd)

    sigma_count 根据做市商弹簧吸收能力自适应:
      capacity_per_sigma = spring * std_usd / volume_bar_threshold
        含义: 价格偏离 1σ 时, 做市商需要对冲多少个 volume bar 的量
        capacity 高 → 做市商能吸收更大偏离 → 更宽 dead zone
      sigma_count = base(3) + max_bonus(3) * tanh(capacity / midpoint(5))
        弱弹簧 → sigma_count ≈ 3 (仅过滤 3σ 噪声)
        强弹簧 → sigma_count → 6 (过滤 6σ, 做市商吸收力强)

    无硬编码 clamp。sigma_count 通过 tanh 自然饱和于 [base, base+max_bonus]。
    """
    axis = safe_float(anchor.get("flip_point"))
    spring = safe_float(anchor.get("spring"))
    if not axis or axis <= 0:
        return None, None, None

    current_price = safe_float(engine.get("price"))
    if not current_price or current_price <= 0:
        fallback_half = axis * (safe_float(CONFIG.get("band_fallback_half_pct")) or 0.005)
        return axis, round(axis - fallback_half, 2), round(axis + fallback_half, 2)

    base_sigma = safe_float(CONFIG.get("band_base_sigma")) or 3.0
    max_bonus = safe_float(CONFIG.get("band_max_sigma_bonus")) or 3.0
    midpoint = safe_float(CONFIG.get("band_spring_capacity_midpoint")) or 5.0
    volume_bar_btc = safe_float(CONFIG.get("volume_bar_threshold_btc")) or 10.0

    # 选择波动率: 优先慢窗 → 快窗(衰减 0.75) → 降级到固定百分比
    std_slow_bps = safe_float(engine.get("std_s_bps"))
    std_fast_bps = safe_float(engine.get("std_f_bps"))

    if std_slow_bps is not None and std_slow_bps > 0:
        std_usd = std_slow_bps / 10000.0 * current_price
    elif std_fast_bps is not None and std_fast_bps > 0:
        std_usd = std_fast_bps / 10000.0 * current_price * 0.75
    else:
        # 降级: 使用固定百分比作为估计波动率
        std_usd = current_price * (safe_float(CONFIG.get("band_fallback_half_pct")) or 0.005) / base_sigma
        # 此时 sigma_count * std_usd = fallback_half_pct * price

    # 弹簧吸收能力: 每 1σ 偏离产生多少 volume bar 的对冲压力
    if spring and spring > 0 and std_usd > 0:
        capacity_per_sigma = spring * std_usd / volume_bar_btc
    else:
        capacity_per_sigma = 0.0

    # 自适应 sigma count (tanh 自然饱和, 无硬 clamp)
    sigma_count = base_sigma + max_bonus * math.tanh(capacity_per_sigma / midpoint)

    band_half = std_usd * sigma_count

    # 安全下限: 不低于价格的 0.1% (约 68 USD for BTC)
    # 这不是 'magic' — 它是防止 std=0 极端情况下带宽归零的工程防护
    absolute_minimum = current_price * 0.001
    if band_half < absolute_minimum:
        band_half = absolute_minimum

    band_lower = round(axis - band_half, 2)
    band_upper = round(axis + band_half, 2)
    return round(axis, 2), band_lower, band_upper


# --- 吸波带状态机 (BandFSM) ---

class BandFiniteStateMachine:
    """
    吸波带四态状态机 (含引力失效 + 迟滞确认):

    状态:
      UNKNOWN         初始状态
      INSIDE          带内约束 (做市商吸波, 动能视为噪声)
      OUTSIDE_ABOVE   带外上方 (真实上行偏离)
      OUTSIDE_BELOW   带外下方 (真实下行偏离)
      GRAVITY_LOST    价格远离轴心, 引力失效 (自由趋势)

    迟滞机制:
      破区 (INSIDE → OUTSIDE): 需连续 N tick 确认 (防针扎)
      回带 (OUTSIDE → INSIDE): 需连续 M tick 确认 (防横跳)
      N < M: 突破事件优先确认, 回归保守确认

    引力失效:
      |price - axis| > axis × gravity_pct → GRAVITY_LOST
    """

    def __init__(self):
        self.state = "UNKNOWN"
        self.break_pending_direction = None
        self.break_pending_count = 0
        self.reenter_pending_count = 0
        self.last_breakout_direction = None
        self.last_breakout_ts = 0

    def update(self, price, axis, band_lower, band_upper):
        """
        输入当前价格和带边界，返回 (state, event)。
        event: "AB_UP" / "AB_DN" / None
        """
        current_price = safe_float(price)
        current_axis = safe_float(axis)
        lower = safe_float(band_lower)
        upper = safe_float(band_upper)

        if current_price is None or current_axis is None or lower is None or upper is None:
            return self.state, None

        gravity_pct = safe_float(CONFIG.get("gravity_lost_pct")) or 0.03
        break_confirm_n = safe_int(CONFIG.get("band_break_confirm_ticks")) or 2
        reenter_confirm_n = safe_int(CONFIG.get("band_reenter_confirm_ticks")) or 3
        event = None

        # 引力失效检测
        if abs(current_price - current_axis) / current_axis > gravity_pct:
            if self.state != "GRAVITY_LOST":
                self.state = "GRAVITY_LOST"
                self.break_pending_direction = None
                self.break_pending_count = 0
                self.reenter_pending_count = 0
            return self.state, None

        # 确定原始位置
        if lower <= current_price <= upper:
            raw_position = "INSIDE"
        elif current_price > upper:
            raw_position = "OUTSIDE_ABOVE"
        else:
            raw_position = "OUTSIDE_BELOW"

        previous_state = self.state

        # ---- 从 INSIDE/UNKNOWN/GRAVITY_LOST 出发的转换 ----
        if previous_state in ("INSIDE", "UNKNOWN", "GRAVITY_LOST"):
            if raw_position == "INSIDE":
                self.state = "INSIDE"
                self.break_pending_direction = None
                self.break_pending_count = 0
                self.reenter_pending_count = 0
            else:
                # 破区待确认
                wanted_direction = "UP" if raw_position == "OUTSIDE_ABOVE" else "DN"
                if self.break_pending_direction == wanted_direction:
                    self.break_pending_count += 1
                else:
                    self.break_pending_direction = wanted_direction
                    self.break_pending_count = 1

                if self.break_pending_count >= break_confirm_n:
                    self.state = raw_position
                    event = "AB_UP" if wanted_direction == "UP" else "AB_DN"
                    self.last_breakout_direction = wanted_direction
                    self.last_breakout_ts = now_ms()
                    self.break_pending_direction = None
                    self.break_pending_count = 0
                    self.reenter_pending_count = 0

        # ---- 从 OUTSIDE 出发的转换 ----
        elif previous_state in ("OUTSIDE_ABOVE", "OUTSIDE_BELOW"):
            if raw_position == previous_state:
                # 仍在同一侧带外
                self.reenter_pending_count = 0
            elif raw_position == "INSIDE":
                # 回带待确认
                self.reenter_pending_count += 1
                if self.reenter_pending_count >= reenter_confirm_n:
                    self.state = "INSIDE"
                    self.last_breakout_direction = None
                    self.reenter_pending_count = 0
                    self.break_pending_direction = None
                    self.break_pending_count = 0
            else:
                # 翻转到另一侧带外 (直接转换)
                self.state = raw_position
                wanted_direction = "UP" if raw_position == "OUTSIDE_ABOVE" else "DN"
                event = "AB_UP" if wanted_direction == "UP" else "AB_DN"
                self.last_breakout_direction = wanted_direction
                self.last_breakout_ts = now_ms()
                self.reenter_pending_count = 0

        return self.state, event


# --- 空间分区 ---

ZONE_LABELS_CN = {
    "BELOW": "中轴下方",
    "VACUUM": "真空区",
    "PRESSURE": "压力区",
    "UNKNOWN": "未知",
}

ZONE_PRIORITY = {
    "BELOW": 3,
    "PRESSURE": 3,
    "VACUUM": 1,
    "UNKNOWN": 0,
}


def classify_spatial_zone(anchor, price):
    """
    空间分区: BELOW / VACUUM / PRESSURE / UNKNOWN
    返回: (zone, pressure_coeff)
    """
    flip_point = safe_float(anchor.get("flip_point"))
    pressure_entry = safe_float(anchor.get("pressure_entry"))
    pressure_node = safe_float(anchor.get("pressure_node"))
    current_price = safe_float(price)

    if not flip_point or not current_price:
        return "UNKNOWN", 0.0
    if current_price < flip_point:
        return "BELOW", 0.0
    if not pressure_entry or not pressure_node:
        return "VACUUM", 0.0
    if current_price < pressure_entry:
        return "VACUUM", 0.0

    denominator = pressure_node - pressure_entry
    if denominator > 0:
        coeff = min((current_price - pressure_entry) / denominator,
                    safe_float(CONFIG.get("pressure_coeff_cap")) or 3.0)
    else:
        coeff = 1.0

    return "PRESSURE", coeff


# --- 资格门 (两层) ---

def check_observation_gate(anchor, engine, zone):
    """
    两层资格门:
      Tier 1 (basic): anchor 可用 + engine 快窗就绪 → 空间事件 (RG/RP) 可用
      Tier 2 (deviation): 在 Tier 1 基础上要求正偏离 > 0.3% → 普通 B/C/R 可用
    返回: (basic_ok, deviation_ok, gate_code, gate_cn)
    """
    if not anchor.get("usable"):
        return False, False, "ANCHOR_UNUSABLE", "中轴不可用"
    if not engine.get("fast_ready"):
        return False, False, "ENGINE_NOT_READY", "快窗未就绪"

    current_price = safe_float(engine.get("price"))
    flip_point = safe_float(anchor.get("flip_point"))
    deviation = compute_deviation_pct(current_price, flip_point)

    if deviation is None:
        return True, False, "DEV_UNKNOWN", "偏离未知"

    threshold = safe_float(CONFIG.get("positive_deviation_threshold")) or 0.003
    if deviation > threshold:
        return True, True, "ELIGIBLE", "可观察"
    if deviation > 0:
        return True, False, "NOISE_ZONE", "引力陷阱区"
    if zone == "BELOW":
        return True, False, "BELOW_SCOPE", "中轴下方"
    return True, False, "OUT_OF_SCOPE", "非目标区"


# --- 动能象限分类 ---

def classify_momentum(engine_state):
    """
    用归一化斜率的符号判断动能象限:
    Q1 (price↑ cvd↑) = B (向上驱动)
    Q2 (price↑/flat cvd↓) = C (顶部衰竭)
    Q3 (price↓ cvd↓) = R (向下驱动)
    Q4 (price↓ cvd↑) = MIXED (底部吸收)
    信号不足 → None
    """
    price_norm = safe_float(engine_state.get("pn_f"))
    cvd_norm = safe_float(engine_state.get("cn_f"))

    if price_norm is None or cvd_norm is None:
        return None, "数据不足"

    min_price_bps = safe_float(CONFIG.get("momentum_min_norm_bps")) or 0.8
    min_cvd_ratio = safe_float(CONFIG.get("momentum_min_cvd_ratio")) or 0.03

    price_up = price_norm > min_price_bps
    price_down = price_norm < -min_price_bps
    cvd_up = cvd_norm > min_cvd_ratio
    cvd_down = cvd_norm < -min_cvd_ratio

    if price_up and cvd_up:
        return "B", "向上驱动"
    if (price_up or (not price_down)) and cvd_down:
        return "C", "顶部衰竭"
    if price_down and cvd_down:
        return "R", "向下驱动"
    if price_down and cvd_up:
        return "MIXED", "底部吸收"
    return None, "信号不足"


# --- B marker 强门控 (提取为独立函数, 消除主循环中的代码重复) ---

def evaluate_b_marker_emission(engine_state):
    """
    B marker 需要更强的确认信号:
      快窗动能 >= 基础阈值 × 倍数
      慢窗 (如就绪) 不能明显转弱
    返回: True if B should be emitted
    """
    price_norm_fast = safe_float(engine_state.get("pn_f"))
    cvd_norm_fast = safe_float(engine_state.get("cn_f"))
    if not price_norm_fast or not cvd_norm_fast:
        return False

    base_bps = safe_float(CONFIG.get("momentum_min_norm_bps")) or 0.8
    multiplier = safe_float(CONFIG.get("b_strength_multiplier")) or 1.5
    min_cvd = safe_float(CONFIG.get("momentum_min_cvd_ratio")) or 0.03

    if price_norm_fast < base_bps * multiplier or cvd_norm_fast < min_cvd * multiplier:
        return False

    # 慢窗背景检查
    if engine_state.get("slow_ready"):
        price_norm_slow = safe_float(engine_state.get("pn_s"))
        cvd_norm_slow = safe_float(engine_state.get("cn_s"))
        if price_norm_slow is not None and price_norm_slow < -0.4:
            return False
        if cvd_norm_slow is not None and cvd_norm_slow < -0.02:
            return False

    return True


# --- 标记状态机 (MarkerTracker) ---

class MarkerTracker:
    """
    统一标记状态机 + 事件微观轨迹追踪。

    规则:
      特殊标记 (RG/RP/AB_UP/AB_DN): 立即确认
      普通标记 (B/C/R): 需连续 N 轮确认
      标记连续 M 轮缺席 → 过期
      事件开始时记录起始快照，持续期间追踪动能变化，结束时输出摘要。
    """

    def __init__(self):
        self.active = None
        self.pending = None
        self.pending_count = 0
        self.absent_count = 0
        self._last_chart_key = None
        self._last_log_key = None
        self._event_context = None

    def update(self, raw_code, zone, priority, is_special=False, snapshot=None):
        """
        输入每轮的原始标记码和上下文快照，返回:
          "new" / "hold" / "expired" / None
        """
        confirm_n = safe_int(CONFIG.get("marker_confirm_rounds")) or 2
        expire_n = safe_int(CONFIG.get("marker_expire_rounds")) or 2

        # 特殊标记: 立即确认
        if is_special and raw_code:
            was_different = (self.active is None or self.active.get("code") != raw_code)
            self.active = {"code": raw_code, "ts": now_ms(), "zone": zone, "priority": priority}
            self.pending = None
            self.pending_count = 0
            self.absent_count = 0
            if was_different:
                self._start_event_context(snapshot)
                return "new"
            self._tick_event_context(snapshot)
            return "hold"

        # 普通标记: 候选确认流程
        if raw_code and raw_code == self.pending:
            self.pending_count += 1
        elif raw_code:
            self.pending = raw_code
            self.pending_count = 1
        else:
            self.pending = None
            self.pending_count = 0

        if self.pending and self.pending_count >= confirm_n:
            if self.active is None or self.active.get("code") != self.pending:
                self.active = {"code": self.pending, "ts": now_ms(), "zone": zone, "priority": priority}
                self.absent_count = 0
                self._start_event_context(snapshot)
                return "new"

        # 过期检测
        if self.active:
            if raw_code != self.active.get("code"):
                self.absent_count += 1
                if self.absent_count >= expire_n:
                    self._end_event_context()
                    self.active = None
                    self._event_context = None
                    return "expired"
            else:
                self.absent_count = 0
            self._tick_event_context(snapshot)
            return "hold"

        return None

    def _start_event_context(self, snapshot):
        """开始追踪新事件的微观轨迹"""
        self._event_context = None
        if not isinstance(snapshot, dict) or not self.active:
            return
        try:
            initial_deviation = safe_float(snapshot.get("dev"))
            self._event_context = {
                "code": self.active["code"],
                "start_ts": self.active["ts"],
                "start_deviation": initial_deviation,
                "start_price": safe_float(snapshot.get("price")),
                "start_momentum": safe_float(snapshot.get("pn_f")),
                "peak_deviation": initial_deviation,
                "trough_deviation": initial_deviation,
                "latest_momentum": safe_float(snapshot.get("pn_f")),
                "tick_count": 0,
            }
        except Exception:
            self._event_context = None

    def _tick_event_context(self, snapshot):
        """更新事件上下文的追踪数据"""
        if not isinstance(self._event_context, dict) or not isinstance(snapshot, dict):
            return
        try:
            current_deviation = safe_float(snapshot.get("dev"))
            self._event_context["tick_count"] = self._event_context.get("tick_count", 0) + 1
            self._event_context["latest_momentum"] = safe_float(snapshot.get("pn_f"))

            if current_deviation is not None:
                peak = safe_float(self._event_context.get("peak_deviation"))
                trough = safe_float(self._event_context.get("trough_deviation"))
                if peak is None or current_deviation > peak:
                    self._event_context["peak_deviation"] = current_deviation
                if trough is None or current_deviation < trough:
                    self._event_context["trough_deviation"] = current_deviation
        except Exception:
            pass

    def _end_event_context(self):
        """事件结束，输出生命周期摘要"""
        if not isinstance(self._event_context, dict):
            return
        try:
            ctx = self._event_context
            duration_ms = now_ms() - (safe_int(ctx.get("start_ts")) or now_ms())
            start_dev = safe_float(ctx.get("start_deviation"))
            peak_dev = safe_float(ctx.get("peak_deviation"))
            trough_dev = safe_float(ctx.get("trough_deviation"))
            start_mom = safe_float(ctx.get("start_momentum"))
            end_mom = safe_float(ctx.get("latest_momentum"))

            deviation_expansion = None
            if start_dev is not None and peak_dev is not None and trough_dev is not None:
                if ctx.get("code") in ("B", "AB_UP"):
                    deviation_expansion = peak_dev - start_dev
                else:
                    deviation_expansion = start_dev - trough_dev

            momentum_change = None
            if start_mom is not None and end_mom is not None:
                momentum_change = end_mom - start_mom

            log_info("[EVT_END] {} dur={:.0f}s ticks={} dev_exp={} mom_chg={}".format(
                ctx.get("code", "?"),
                duration_ms / 1000,
                ctx.get("tick_count", 0),
                "{:.1f}bps".format(deviation_expansion * 10000) if deviation_expansion is not None else "-",
                "{:.2f}".format(momentum_change) if momentum_change is not None else "-",
            ))
        except Exception:
            pass

    def get_active_code(self):
        """获取当前活跃标记的 code"""
        if self.active:
            return self.active["code"]
        return None

    def get_live_health(self):
        """
        获取活跃事件的实时健康度指标 (每 tick 调用)。
        返回: dict with {dev_expansion, momentum_change, tick_count} 或 None
        供 classify_scenario 实时消费, 用于假突破/延续判定。
        """
        if not isinstance(self._event_context, dict):
            return None
        try:
            ctx = self._event_context
            start_dev = safe_float(ctx.get("start_deviation"))
            peak_dev = safe_float(ctx.get("peak_deviation"))
            trough_dev = safe_float(ctx.get("trough_deviation"))
            start_mom = safe_float(ctx.get("start_momentum"))
            latest_mom = safe_float(ctx.get("latest_momentum"))
            code = ctx.get("code", "")

            dev_expansion = None
            if start_dev is not None and peak_dev is not None and trough_dev is not None:
                if code in ("B", "AB_UP"):
                    dev_expansion = peak_dev - start_dev
                else:
                    dev_expansion = start_dev - trough_dev

            momentum_change = None
            if start_mom is not None and latest_mom is not None:
                momentum_change = latest_mom - start_mom

            return {
                "dev_expansion": dev_expansion,
                "momentum_change": momentum_change,
                "tick_count": ctx.get("tick_count", 0),
            }
        except Exception:
            return None

    def get_chart_event(self):
        """获取需要在图表上打 flag 的事件 (去重)"""
        if not self.active:
            return None
        key = (self.active["code"], self.active["ts"])
        if key == self._last_chart_key:
            return None
        self._last_chart_key = key
        return self.active

    def get_log_event(self):
        """获取需要写日志的事件 (去重)"""
        if not self.active:
            return None
        key = (self.active["code"], self.active["ts"])
        if key == self._last_log_key:
            return None
        self._last_log_key = key
        return self.active

    def reset_for_spatial_transition(self):
        """空间跃迁时重置状态机，允许新事件重新触发"""
        if self._event_context and self.active:
            self._end_event_context()
        self.active = None
        self.pending = None
        self.pending_count = 0
        self.absent_count = 0
        self._last_chart_key = None
        self._last_log_key = None
        self._event_context = None


# --- 空间事件追踪 ---

class SpatialTracker:
    """追踪空间区域跃迁和首次事件 (RG / RP)"""

    def __init__(self):
        self.last_zone = None
        self.last_pressure_touch_ms = 0
        self.last_gamma_break_ms = 0
        self.gamma_r_armed = False
        self.pressure_r_armed = False

    def update(self, zone, marker_tracker):
        """检测空间跃迁，arm 特殊事件。返回 (first_pressure, first_gamma)"""
        window_ms = int((safe_float(CONFIG.get("spatial_window_hours")) or 4.0) * 3600000)
        current_ms = now_ms()
        first_pressure = False
        first_gamma = False

        if self.last_zone is not None:
            # 首次进入压力区
            if self.last_zone != "PRESSURE" and zone == "PRESSURE":
                if self.last_pressure_touch_ms == 0 or (current_ms - self.last_pressure_touch_ms) > window_ms:
                    first_pressure = True
                    marker_tracker.reset_for_spatial_transition()
                    self.pressure_r_armed = True
                self.last_pressure_touch_ms = current_ms

            # 首次跌破中轴
            if self.last_zone != "BELOW" and zone == "BELOW":
                if self.last_gamma_break_ms == 0 or (current_ms - self.last_gamma_break_ms) > window_ms:
                    first_gamma = True
                    marker_tracker.reset_for_spatial_transition()
                    self.gamma_r_armed = True
                self.last_gamma_break_ms = current_ms

        # 离开区域时 disarm
        if zone != "BELOW":
            self.gamma_r_armed = False
        if zone != "PRESSURE":
            self.pressure_r_armed = False

        self.last_zone = zone
        return first_pressure, first_gamma

    def check_special_event(self, momentum_code, zone):
        """检查是否触发 RG / RP 特殊事件"""
        if self.gamma_r_armed and zone == "BELOW" and momentum_code == "R":
            self.gamma_r_armed = False
            return "RG"
        if self.pressure_r_armed and zone == "PRESSURE" and momentum_code == "R":
            self.pressure_r_armed = False
            return "RP"
        return None


# --- 场景分类器 ---

def classify_scenario(band_state, band_fsm, zone, momentum_code,
                      marker_tracker, deviation_velocity, hedging_at_price):
    """
    七维场景判定 (v6: 含实时健康度消费)。
    优先级: 引力失效 > 吸波带 > 压力/中轴。

    ABSORPTION_REJECTION 的判定现在使用 MarkerTracker 的实时健康度:
    当破区后偏离停止扩展 (dev_expansion ≤ 0) 且动能反转 (momentum_change 反向),
    立刻判定为假突破, 无需等待事件过期。
    """
    active_code = marker_tracker.get_active_code()
    dev_vel = safe_float(deviation_velocity)
    hedging_val = safe_float(hedging_at_price)
    live_health = marker_tracker.get_live_health()

    # 引力失效 → 自由趋势
    if band_state == "GRAVITY_LOST":
        return "TREND_VACUUM", "引力失效自由趋势"

    # 带内 → 绝对静默约束
    if band_state == "INSIDE":
        return "ABSORPTION_TRAPPED", "吸波带约束态"

    # 带外 + 有近期破区方向 → 吸波带破区场景
    breakout_direction = band_fsm.last_breakout_direction
    if breakout_direction:
        # 实时健康度判定: 偏离是否仍在扩展, 动能是否反转
        if isinstance(live_health, dict) and live_health.get("tick_count", 0) >= 2:
            dev_exp = safe_float(live_health.get("dev_expansion"))
            mom_chg = safe_float(live_health.get("momentum_change"))

            # 假突破: 偏离已停止扩展 + 动能向破区反方向变化
            if dev_exp is not None and dev_exp <= 0:
                if breakout_direction == "UP" and momentum_code in ("C", "R"):
                    return "ABSORPTION_REJECTION", "破区假突破(偏离收缩+动能反转)"
                if breakout_direction == "DN" and momentum_code in ("B", "MIXED"):
                    return "ABSORPTION_REJECTION", "破区假突破(偏离收缩+动能反转)"

            # 动能剧烈反转 (即使偏离还在扩展)
            if mom_chg is not None:
                if breakout_direction == "UP" and mom_chg < -1.0 and momentum_code in ("C", "R"):
                    return "ABSORPTION_REJECTION", "破区假突破(动能骤降)"
                if breakout_direction == "DN" and mom_chg > 1.0 and momentum_code in ("B", "MIXED"):
                    return "ABSORPTION_REJECTION", "破区假突破(动能骤升)"

        # 趋势延续: 动能方向与破区方向一致
        is_extending = (
            (breakout_direction == "UP" and momentum_code == "B")
            or (breakout_direction == "DN" and momentum_code == "R")
        )
        if is_extending:
            return "ABSORPTION_BREAKOUT_EXTENDING", "破区延续"

        # 基础假突破判定 (无需健康度, 仅靠动能方向)
        is_rejecting = (
            (breakout_direction == "UP" and momentum_code in ("C", "R"))
            or (breakout_direction == "DN" and momentum_code in ("B", "MIXED"))
        )
        if is_rejecting:
            return "ABSORPTION_REJECTION", "破区假突破"

    # 压力区场景
    if zone == "PRESSURE" and active_code == "B" and momentum_code == "B":
        if dev_vel is not None and dev_vel > 0 and hedging_val is not None and hedging_val < -500:
            return "PRESSURE_BREAKOUT_SUSTAINED", "压力突破延续"
    if zone == "PRESSURE" and active_code in ("RP", "R") and momentum_code == "R":
        return "PRESSURE_REJECTION", "压力承压回落"

    # 中轴跌破场景
    if zone == "BELOW" and active_code in ("R", "RG") and momentum_code == "R":
        if dev_vel is not None and dev_vel < 0:
            return "GAMMA_BREAKDOWN_EXTENDING", "中轴跌破延伸"

    return None, None


# --- 带外标记生成 (提取为函数, 消除主循环重复代码) ---

def generate_outside_band_marker(engine, basic_gate_ok, deviation_gate_ok,
                                 momentum_code, spatial_tracker, zone):
    """
    在带外（包括 GRAVITY_LOST）时的标记生成逻辑。
    返回: (raw_code, is_special)
    """
    # 空间特殊事件 (需 basic gate + momentum)
    if basic_gate_ok and momentum_code:
        spatial_code = spatial_tracker.check_special_event(momentum_code, zone)
        if spatial_code:
            return spatial_code, True

    # 普通标记 (需 deviation gate)
    if deviation_gate_ok and momentum_code in ("B", "C", "R"):
        if momentum_code == "B":
            if evaluate_b_marker_emission(engine):
                return "B", False
            return None, False
        return momentum_code, False

    return None, False


# ================================================================
# Display
# ================================================================
class Display:
    """FMZ 平台展示层: Chart / LogStatus / Log"""

    def __init__(self):
        self._chart_object = None
        self._chart_initialized = False
        self._last_chart_update_sec = 0
        self._last_logprofit_sec = 0
        self._last_summary_sec = 0
        self._last_event_log_key = None

    def init_exchange(self):
        """初始化交易所精度设置"""
        exchange_obj = globals().get("exchange")
        if exchange_obj and hasattr(exchange_obj, "SetPrecision"):
            try:
                exchange_obj.SetPrecision(
                    CONFIG.get("exchange_price_precision", 2),
                    CONFIG.get("exchange_amount_precision", 4),
                )
            except Exception:
                pass

    # --- Chart ---

    def update_chart(self, engine, band_lower, band_upper, marker_tracker, scenario_flag=None):
        """
        更新 FMZ 图表: 成交价 + 吸波带边界 + 事件/场景 flags

        scenario_flag: 高价值场景变化时的追打标记 (如 REJECTION/EXTENDING)
        """
        now_sec = int(time.time())
        if now_sec - self._last_chart_update_sec < CONFIG.get("chart_update_interval_sec", 10):
            return

        current_price = safe_float(engine.get("price"))
        if not current_price:
            return

        chart_fn = _get_fmz_function("Chart")
        if not chart_fn:
            return

        timestamp_ms = int(time.time() * 1000)
        try:
            if not self._chart_initialized:
                chart_config = {
                    "title": {"text": "Gamma Spatial Observer"},
                    "xAxis": {"type": "datetime"},
                    "series": [
                        {"id": "price", "name": "成交价", "data": [], "color": "#2196F3"},
                        {"name": "吸波上沿", "data": [], "dashStyle": "ShortDash", "color": "#FF9800", "lineWidth": 1},
                        {"name": "吸波下沿", "data": [], "dashStyle": "ShortDash", "color": "#FF9800", "lineWidth": 1},
                        {"type": "flags", "name": "事件", "onSeries": "price", "data": []},
                    ],
                }
                self._chart_object = chart_fn(chart_config)
                if self._chart_object and hasattr(self._chart_object, "reset"):
                    self._chart_object.reset()
                self._chart_initialized = True

            if self._chart_object and hasattr(self._chart_object, "add"):
                self._chart_object.add(0, [timestamp_ms, current_price])

                upper_val = safe_float(band_upper)
                lower_val = safe_float(band_lower)
                if upper_val:
                    self._chart_object.add(1, [timestamp_ms, upper_val])
                if lower_val:
                    self._chart_object.add(2, [timestamp_ms, lower_val])

                # 标记事件 flag (破区/空间事件起点)
                chart_event = marker_tracker.get_chart_event()
                if chart_event and chart_event.get("priority", 0) >= 2:
                    flag_text_map = {
                        "B": "向上驱动", "C": "衰竭", "R": "回归",
                        "RG": "首跌中轴R", "RP": "首触压力R",
                        "AB_UP": "破区↑", "AB_DN": "破区↓",
                    }
                    self._chart_object.add(3, {
                        "x": chart_event["ts"],
                        "title": chart_event["code"],
                        "text": flag_text_map.get(chart_event["code"], chart_event["code"]),
                    })

                # 高阶场景追打 flag (假突破/延续确认)
                if scenario_flag:
                    scenario_flag_map = {
                        "ABSORPTION_REJECTION": "假突破!",
                        "ABSORPTION_BREAKOUT_EXTENDING": "延续✓",
                        "PRESSURE_REJECTION": "压力承压",
                        "GAMMA_BREAKDOWN_EXTENDING": "跌破延伸",
                    }
                    flag_text = scenario_flag_map.get(scenario_flag)
                    if flag_text:
                        self._chart_object.add(3, {
                            "x": timestamp_ms,
                            "title": scenario_flag[:3],
                            "text": flag_text,
                        })

                self._last_chart_update_sec = now_sec
        except Exception as e:
            log_warn("chart update fail: " + str(e))

    # --- LogProfit ---

    def update_logprofit(self, anchor, engine):
        """输出偏离度到 FMZ LogProfit 曲线"""
        current_price = safe_float(engine.get("price"))
        flip_point = safe_float(anchor.get("flip_point"))
        deviation = compute_deviation_pct(current_price, flip_point)
        if deviation is None:
            return

        now_sec = int(time.time())
        if now_sec - self._last_logprofit_sec < CONFIG.get("logprofit_min_interval_sec", 10):
            return

        logprofit_fn = _get_fmz_function("LogProfit")
        if logprofit_fn:
            try:
                logprofit_fn(round(deviation * 100.0, 3))
                self._last_logprofit_sec = now_sec
            except Exception:
                pass

    # --- LogStatus (状态栏) ---

    def update_status(self, anchor, engine, gate_info, momentum_info, zone, pressure_coeff,
                      band_state, band_lower, band_upper, band_fsm,
                      marker_tracker, spatial_tracker, scenario,
                      deviation_velocity, hedging_at_price):
        """渲染 FMZ 状态栏"""
        try:
            _, _, gate_code, gate_cn = gate_info
            momentum_code, momentum_cn = momentum_info
            active_marker = marker_tracker.get_active_code() or "-"
            scenario_code, scenario_cn = scenario
            current_price = safe_float(engine.get("price"))
            flip_point = safe_float(anchor.get("flip_point"))
            deviation = compute_deviation_pct(current_price, flip_point)

            lower_val = safe_float(band_lower)
            upper_val = safe_float(band_upper)
            band_width = (upper_val - lower_val) if upper_val and lower_val else 0

            summary = "价={}｜偏离={}｜带={}({}USD)｜标记={}｜场景={}".format(
                fmt_price(current_price), fmt_percent(deviation),
                band_state, fmt_number(band_width, 0),
                active_marker, scenario_code or "-")

            live_health = marker_tracker.get_live_health()
            health_dev_exp = "-"
            health_mom_chg = "-"
            if isinstance(live_health, dict):
                de = safe_float(live_health.get("dev_expansion"))
                mc_h = safe_float(live_health.get("momentum_change"))
                health_dev_exp = "{:.1f}bps".format(de * 10000) if de is not None else "-"
                health_mom_chg = fmt_number(mc_h, 2) if mc_h is not None else "-"

            table_anchor = {
                "type": "table",
                "title": "空间锚",
                "cols": ["字段", "值", "说明"],
                "rows": [
                    ["Gamma中轴(=吸波轴)", fmt_price(flip_point),
                     "{}/{}".format(anchor.get("status", "?"), anchor.get("mode", "?"))],
                    ["数据时间", fmt_timestamp(anchor.get("source_ts_ms")), ""],
                    ["弹簧系数", fmt_number(anchor.get("spring"), 4), "BTC/USD"],
                    ["压力节点", fmt_price(anchor.get("pressure_node")), ""],
                    ["压力入口", fmt_price(anchor.get("pressure_entry")), "动态梯度推导"],
                    ["吸波带", "{} ~ {}".format(fmt_price(band_lower), fmt_price(band_upper)),
                     "宽={}USD".format(fmt_number(band_width, 0))],
                ],
            }

            table_factors = {
                "type": "table",
                "title": "因子观察",
                "cols": ["字段", "值", "说明"],
                "rows": [
                    ["成交价", fmt_price(current_price), ""],
                    ["偏离", fmt_percent(deviation), ""],
                    ["偏离速度", fmt_number(deviation_velocity, 5) if deviation_velocity is not None else "-", "Δdev/tick"],
                    ["快窗动能", fmt_number(engine.get("pn_f"), 2), "bps/bar"],
                    ["快窗CVD", fmt_number(engine.get("cn_f"), 4), ""],
                    ["慢窗动能", fmt_number(engine.get("pn_s"), 2), "bps/bar"],
                    ["快窗std", fmt_number(engine.get("std_f_bps"), 2), "bps"],
                    ["慢窗std", fmt_number(engine.get("std_s_bps"), 2), "bps"],
                    ["微观对冲", fmt_number(hedging_at_price, 1), "BTC"],
                    ["柱进度", "{}/{}".format(engine.get("bar_count", 0), CONFIG.get("slow_window", 30)), ""],
                ],
            }

            table_state_machine = {
                "type": "table",
                "title": "状态机",
                "cols": ["字段", "值", "说明"],
                "rows": [
                    ["吸波带状态", band_state, "INSIDE静默 OUTSIDE观测 GRAVITY_LOST自由"],
                    ["破区方向", band_fsm.last_breakout_direction or "-", ""],
                    ["空间区划", zone, ZONE_LABELS_CN.get(zone, "?")],
                    ["压力系数", fmt_number(pressure_coeff, 3), ""],
                    ["资格门", gate_code, gate_cn],
                    ["动能", (momentum_code or "-") + " " + momentum_cn, ""],
                    ["确认标记", active_marker, ""],
                    ["场景", scenario_code or "-", scenario_cn or ""],
                    ["实时偏离扩展", health_dev_exp, "破区追踪"],
                    ["实时动能变化", health_mom_chg, "破区追踪"],
                    ["gamma_r", fmt_yes_no(spatial_tracker.gamma_r_armed), ""],
                    ["pressure_r", fmt_yes_no(spatial_tracker.pressure_r_armed), ""],
                ],
            }

            ladder = anchor.get("ladder", [])
            ladder_rows = []
            for item in ladder:
                if isinstance(item, dict):
                    ladder_rows.append([
                        "{:+d}%".format(safe_int(item.get("move_pct")) or 0),
                        fmt_price(item.get("price")),
                        "{} BTC".format(fmt_number(item.get("hedging_btc"), 1)),
                    ])
            table_ladder = {
                "type": "table",
                "title": "对冲阶梯",
                "cols": ["偏移", "价格", "对冲量"],
                "rows": ladder_rows,
            }

            log_status(summary, tables=[table_anchor, table_factors, table_state_machine, table_ladder])
        except Exception as e:
            log_warn("status render fail: " + str(e))

    # --- Logs ---

    def emit_state_change_log(self, gate_info, momentum_info, zone, band_state, deviation):
        """状态变化日志 (去重)"""
        key = (gate_info[2], momentum_info[0], zone, band_state)
        if key == self._last_event_log_key:
            return
        self._last_event_log_key = key
        log_info("状态: 带={} 动能={} 资格={} 空间={} 偏离={}".format(
            band_state, momentum_info[0] or "-", gate_info[2], zone, fmt_percent(deviation)))

    def emit_marker_log(self, event, anchor, engine, zone, band_state):
        """标记确认日志"""
        if not event:
            return
        current_price = safe_float(engine.get("price"))
        flip_point = safe_float(anchor.get("flip_point"))
        deviation = compute_deviation_pct(current_price, flip_point)
        log_info("[MARKER] {} | 价={} | 偏离={} | 空间={} | 带={}".format(
            event["code"], fmt_price(current_price), fmt_percent(deviation), zone, band_state))

    def emit_scenario_log(self, scenario):
        """场景变化日志"""
        scenario_code, scenario_cn = scenario
        if scenario_code:
            log_info("[SCENARIO] {} ({})".format(scenario_code, scenario_cn))

    def emit_periodic_summary(self, anchor, engine, momentum_info, gate_info,
                              band_state, band_lower, band_upper):
        """定期综述日志"""
        now_sec = int(time.time())
        if now_sec - self._last_summary_sec < CONFIG.get("summary_log_interval_sec", 60):
            return
        self._last_summary_sec = now_sec

        deviation = compute_deviation_pct(safe_float(engine.get("price")), safe_float(anchor.get("flip_point")))
        log_info("综述: 轴={} 价={} 偏离={} 带={}({}-{}) spring={} 快(p={},c={}) std_s={}".format(
            fmt_price(anchor.get("flip_point")),
            fmt_price(engine.get("price")),
            fmt_percent(deviation),
            band_state,
            fmt_price(band_lower),
            fmt_price(band_upper),
            fmt_number(anchor.get("spring"), 4),
            fmt_number(engine.get("pn_f"), 2),
            fmt_number(engine.get("cn_f"), 3),
            fmt_number(engine.get("std_s_bps"), 2),
        ))


# ================================================================
# Main Loop
# ================================================================
def run():
    """主策略循环"""
    anchor_layer = Anchor()
    engine_layer = Engine()
    band_fsm = BandFiniteStateMachine()
    marker_tracker = MarkerTracker()
    spatial_tracker = SpatialTracker()
    display = Display()
    display.init_exchange()

    previous_deviation = None
    previous_flip_point = None
    last_scenario = (None, None)

    while True:
        try:
            # === 数据采集 ===
            anchor = anchor_layer.update()
            engine = engine_layer.poll()
            current_price = safe_float(engine.get("price"))
            flip_point = safe_float(anchor.get("flip_point"))

            # === 偏离度及其速度 ===
            deviation = compute_deviation_pct(current_price, flip_point)
            deviation_velocity = None
            if (deviation is not None and previous_deviation is not None
                    and flip_point and previous_flip_point):
                if abs(flip_point - previous_flip_point) / previous_flip_point < 0.001:
                    deviation_velocity = deviation - previous_deviation
            previous_deviation = deviation
            previous_flip_point = flip_point

            # === 微观对冲插值 ===
            hedging_at_price = anchor_layer.interpolate_hedging(current_price) if current_price else None

            # === 吸波带计算 ===
            absorption_axis, band_lower, band_upper = compute_absorption_band(anchor, engine)

            # === 吸波带状态机更新 ===
            band_state, band_event = band_fsm.update(current_price, absorption_axis, band_lower, band_upper)

            # === 空间分区 ===
            zone, pressure_coeff = classify_spatial_zone(anchor, current_price)

            # === 资格门 ===
            basic_gate_ok, deviation_gate_ok, gate_code, gate_cn = check_observation_gate(anchor, engine, zone)
            gate_info = (basic_gate_ok, deviation_gate_ok, gate_code, gate_cn)

            # === 空间事件追踪 (仅带外且非引力失效时) ===
            if band_state not in ("INSIDE", "GRAVITY_LOST"):
                spatial_tracker.update(zone, marker_tracker)

            # === 动能象限分类 ===
            if basic_gate_ok:
                momentum_code, momentum_cn = classify_momentum(engine)
            else:
                momentum_code, momentum_cn = None, "基础资格不足"
            momentum_info = (momentum_code, momentum_cn)

            # === 标记生成 ===
            raw_marker_code = None
            is_special_marker = False

            if band_state == "INSIDE":
                # 带内: 绝对静默, 所有动能视为噪声
                raw_marker_code = None

            elif band_event:
                # 破区事件: 最高优先级, 立即确认
                raw_marker_code = band_event
                is_special_marker = True

            elif band_state == "GRAVITY_LOST":
                # 引力失效: 正常观测, 但不产生 band 事件
                raw_marker_code, is_special_marker = generate_outside_band_marker(
                    engine, basic_gate_ok, deviation_gate_ok, momentum_code, spatial_tracker, zone)

            else:
                # 带外: 正常观测
                raw_marker_code, is_special_marker = generate_outside_band_marker(
                    engine, basic_gate_ok, deviation_gate_ok, momentum_code, spatial_tracker, zone)

            # === 上下文快照 ===
            context_snapshot = {
                "dev": deviation,
                "price": current_price,
                "pn_f": safe_float(engine.get("pn_f")),
                "cn_f": safe_float(engine.get("cn_f")),
            }

            # === 标记优先级 ===
            marker_priority = 3 if is_special_marker else ZONE_PRIORITY.get(zone, 0)

            # === 标记状态机更新 ===
            tracker_result = marker_tracker.update(
                raw_marker_code, zone, marker_priority, is_special_marker, context_snapshot)

            # === 场景分类 ===
            scenario = classify_scenario(
                band_state, band_fsm, zone, momentum_code,
                marker_tracker, deviation_velocity, hedging_at_price)

            # 场景变化时的 flag 追打
            scenario_chart_flag = None
            if scenario != last_scenario and scenario[0] is not None:
                display.emit_scenario_log(scenario)
                # 高价值场景: 在图表上追打标记
                if scenario[0] in ("ABSORPTION_REJECTION", "ABSORPTION_BREAKOUT_EXTENDING",
                                   "PRESSURE_REJECTION", "GAMMA_BREAKDOWN_EXTENDING"):
                    scenario_chart_flag = scenario[0]
            last_scenario = scenario

            # === 展示层输出 ===
            display.update_chart(engine, band_lower, band_upper, marker_tracker, scenario_chart_flag)
            display.update_logprofit(anchor, engine)
            display.update_status(
                anchor, engine, gate_info, momentum_info, zone, pressure_coeff,
                band_state, band_lower, band_upper, band_fsm,
                marker_tracker, spatial_tracker, scenario,
                deviation_velocity, hedging_at_price)

            display.emit_state_change_log(gate_info, momentum_info, zone, band_state, deviation)

            if tracker_result == "new":
                event = marker_tracker.get_log_event()
                display.emit_marker_log(event, anchor, engine, zone, band_state)

            display.emit_periodic_summary(
                anchor, engine, momentum_info, gate_info,
                band_state, band_lower, band_upper)

        except Exception as e:
            log_error("loop exception: " + str(e))
            log_error(traceback.format_exc())

        time.sleep(safe_float(CONFIG.get("loop_sleep_sec")) or 2.0)


def main():
    """程序入口"""
    try:
        run()
    except Exception as e:
        log_error("main exception: " + str(e))
        log_error(traceback.format_exc())


if __name__ == "__main__":
    main()
