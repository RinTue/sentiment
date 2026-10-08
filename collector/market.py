"""Tầng 4 trên máy chủ: chỉ số tâm lý tổng hợp mỗi giờ, Coinbase Premium, stablecoin và kiểm chứng Fear & Greed.

Công thức giống hệt trang web để hai bên khớp nhau. Mỗi thành phần có nguồn dự phòng vì máy chủ GitHub
đặt ở Mỹ, nơi Bybit và một số sàn chặn truy cập.
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
COMPOSITE_FILE = os.path.join(DATA, "composite.csv")
BTC_DAILY_FILE = os.path.join(DATA, "btc_daily.csv")
BACKTEST_FILE = os.path.join(DATA, "backtest.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
COLS = ["time_utc", "value", "text", "fng", "votes", "funding", "ls", "pc", "wiki", "btc", "premium24", "stable_bn", "n_parts"]


def get(url: str, params: dict | None = None, timeout: int = 15):
    r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def pct_rank(arr: list, v: float) -> float:
    return 100 * sum(1 for x in arr if x <= v) / len(arr)


# ---------------------------------------------------------------- nguồn dữ liệu
def fng(limit: int = 10) -> list:
    j = get("https://api.alternative.me/fng/", {"limit": limit})
    return sorted((int(d["timestamp"]), int(d["value"]), d["value_classification"]) for d in j["data"])


def votes() -> float:
    j = get("https://api.coingecko.com/api/v3/coins/bitcoin", {"localization": "false", "tickers": "false", "market_data": "false",
                                                                "community_data": "true", "developer_data": "false"})
    v = j.get("sentiment_votes_up_percentage")
    if v is None:
        raise ValueError("không có phiếu")
    return float(v)


def funding_bybit() -> list:
    out, end = [], None
    for _ in range(2):
        p = {"category": "linear", "symbol": "BTCUSDT", "limit": 200}
        if end:
            p["endTime"] = end
        j = get("https://api.bybit.com/v5/market/funding/history", p, 10)
        if j.get("retCode") != 0:
            raise ValueError("Bybit: " + str(j.get("retMsg")))
        lst = j["result"]["list"]
        out += [(int(d["fundingRateTimestamp"]), float(d["fundingRate"])) for d in lst]
        if not lst:
            break
        end = int(lst[-1]["fundingRateTimestamp"]) - 1
    return out


def funding_okx() -> list:
    out, after = [], None
    for _ in range(3):
        p = {"instId": "BTC-USDT-SWAP", "limit": 100}
        if after:
            p["after"] = after
        j = get("https://www.okx.com/api/v5/public/funding-rate-history", p, 10)
        if j.get("code") != "0":
            raise ValueError("OKX: " + str(j.get("msg")))
        lst = j["data"]
        out += [(int(d["fundingTime"]), float(d.get("realizedRate") or d["fundingRate"])) for d in lst]
        if len(lst) < 100:
            break
        after = lst[-1]["fundingTime"]
    return out


def funding_hyperliquid() -> list:
    """Hyperliquid trả funding mỗi giờ; nhân 8 để cùng thang với các sàn tính 8 giờ một lần."""
    out, start = [], int((time.time() - 90 * 86400) * 1000)
    for _ in range(6):
        r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "fundingHistory", "coin": "BTC", "startTime": start},
                          headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
        lst = r.json()
        out += [(int(d["time"]), float(d["fundingRate"]) * 8) for d in lst]
        if len(lst) < 500:
            break
        start = int(lst[-1]["time"]) + 1
    return out


def long_short() -> tuple[float, str]:
    try:
        j = get("https://api.bybit.com/v5/market/account-ratio", {"category": "linear", "symbol": "BTCUSDT", "period": "1d", "limit": 2}, 10)
        if j.get("retCode") == 0 and j["result"]["list"]:
            return float(j["result"]["list"][0]["buyRatio"]), "Bybit"
    except Exception:  # noqa: BLE001
        pass
    j = get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract", {"instId": "BTC-USDT-SWAP", "period": "1D", "limit": 2}, 10)
    if j.get("code") != "0" or not j["data"]:
        raise ValueError("OKX: " + str(j.get("msg")))
    r = float(j["data"][0][1])
    return r / (1 + r), "OKX"


def put_call() -> float:
    j = get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency", {"currency": "BTC", "kind": "option"})
    p = c = 0.0
    for o in j["result"]:
        oi = float(o.get("open_interest") or 0)
        if o["instrument_name"].endswith("-P"):
            p += oi
        else:
            c += oi
    if not c:
        raise ValueError("không có quyền chọn")
    return p / c


def wiki_z() -> tuple[float, int]:
    end, start = datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc) - timedelta(days=91)
    j = get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Bitcoin/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    v = [d["views"] for d in j["items"]]
    if len(v) < 40:
        raise ValueError("thiếu dữ liệu")
    last, prior = v[-3:], v[-63:-3]
    m = sum(prior) / len(prior)
    sd = math.sqrt(sum((x - m) ** 2 for x in prior) / len(prior)) or 1
    return (sum(last) / 3 - m) / sd, v[-1]


def coinbase_hourly() -> dict:
    j = get("https://api.exchange.coinbase.com/products/BTC-USD/candles", {"granularity": 3600})
    return {int(r[0]): float(r[4]) for r in j}


def usdt_hourly() -> tuple[dict, str]:
    """Giá BTC/USDT ở sàn quốc tế, để so với giá BTC/USD trên Coinbase."""
    errs = []
    try:
        j = get("https://api.bybit.com/v5/market/kline", {"category": "spot", "symbol": "BTCUSDT", "interval": "60", "limit": 200}, 10)
        if j.get("retCode") == 0:
            return {int(r[0]) // 1000: float(r[4]) for r in j["result"]["list"]}, "Bybit"
        errs.append(str(j.get("retMsg")))
    except Exception as ex:  # noqa: BLE001
        errs.append(str(ex)[:60])
    try:
        j = get("https://www.okx.com/api/v5/market/candles", {"instId": "BTC-USDT", "bar": "1H", "limit": 300}, 10)
        if j.get("code") == "0":
            return {int(r[0]) // 1000: float(r[4]) for r in j["data"]}, "OKX"
        errs.append(str(j.get("msg")))
    except Exception as ex:  # noqa: BLE001
        errs.append(str(ex)[:60])
    j = get("https://api.kraken.com/0/public/OHLC", {"pair": "XBTUSDT", "interval": 60})
    res = j.get("result") or {}
    key = next((k for k in res if k != "last"), None)
    if not key:
        raise ValueError("; ".join(errs + [str(j.get("error"))]))
    return {int(r[0]): float(r[4]) for r in res[key]}, "Kraken"


def stablecoins() -> list:
    j = get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=25)
    out = []
    for d in j:
        v = ((d.get("totalCirculatingUSD") or {}).get("peggedUSD"))
        if v:
            out.append((int(d["date"]), float(v)))
    if len(out) < 40:
        raise ValueError("thiếu dữ liệu")
    return out


# ---------------------------------------------------------------- kiểm chứng Fear & Greed
def btc_daily() -> dict:
    """Giá đóng cửa ngày (UTC) của BTC trên Coinbase từ 2018, lưu dần vào data/btc_daily.csv."""
    have = {}
    if os.path.exists(BTC_DAILY_FILE):
        with open(BTC_DAILY_FILE, encoding="utf-8") as f:
            for row in list(csv.reader(f))[1:]:
                have[int(row[0])] = float(row[1])
    start = max(have) - 5 * 86400 if have else int(datetime(2018, 1, 15, tzinfo=timezone.utc).timestamp())
    now = int(time.time())
    while start < now - 86400:
        end = min(start + 290 * 86400, now)
        j = get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                {"granularity": 86400, "start": datetime.fromtimestamp(start, timezone.utc).isoformat(), "end": datetime.fromtimestamp(end, timezone.utc).isoformat()}, 20)
        for r in j:
            if int(r[0]) < now - 86400:      # bỏ cây nến của ngày chưa đóng
                have[int(r[0])] = float(r[4])
        start = end
        time.sleep(0.4)
    with open(BTC_DAILY_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day_utc", "close"])
        for t in sorted(have):
            w.writerow([t, have[t]])
    return have


BUCKETS = [(0, 20, "Sợ hãi cực độ"), (20, 40, "Sợ hãi"), (40, 60, "Trung tính"), (60, 80, "Tham lam"), (80, 101, "Tham lam cực độ")]


def backtest(today: str) -> dict:
    try:
        with open(BACKTEST_FILE, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("day") == today and old.get("fng"):
            return old
    except (OSError, json.JSONDecodeError):
        pass
    px = btc_daily()
    rows = fng(0)
    def stats(rets: list) -> dict:
        if not rets:
            return {"n": 0}
        return {"n": len(rets), "up": round(100 * sum(1 for r in rets if r > 0) / len(rets)), "med": round(statistics.median(rets), 1),
                "avg": round(sum(rets) / len(rets), 1), "worst": round(min(rets), 1), "best": round(max(rets), 1)}
    out = {"day": today, "start": None, "fng": {"buckets": [], "all": {}}}
    pairs = []
    for t, v, _ in rows:
        d = t - t % 86400
        if d in px:
            r7 = (px[d + 7 * 86400] / px[d] - 1) * 100 if d + 7 * 86400 in px else None
            r30 = (px[d + 30 * 86400] / px[d] - 1) * 100 if d + 30 * 86400 in px else None
            pairs.append((d, v, r7, r30))
    if not pairs:
        raise ValueError("không ghép được F&G với giá")
    out["start"] = datetime.fromtimestamp(pairs[0][0], timezone.utc).strftime("%Y-%m-%d")
    out["days"] = len(pairs)
    for lo, hi, name in BUCKETS:
        sel = [p for p in pairs if lo <= p[1] < hi]
        out["fng"]["buckets"].append({"name": name, "lo": lo, "hi": hi, "d7": stats([p[2] for p in sel if p[2] is not None]),
                                      "d30": stats([p[3] for p in sel if p[3] is not None])})
    out["fng"]["all"] = {"d7": stats([p[2] for p in pairs if p[2] is not None]), "d30": stats([p[3] for p in pairs if p[3] is not None])}
    with open(BACKTEST_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


# ---------------------------------------------------------------- chỉ số tổng hợp
def try_(errs: dict, key: str, fn, *a):
    try:
        return fn(*a)
    except Exception as ex:  # noqa: BLE001
        errs[key] = str(ex)[:120]
        return None


def read_composite() -> list:
    if not os.path.exists(COMPOSITE_FILE):
        return []
    with open(COMPOSITE_FILE, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run(latest: dict, text_history: list) -> dict:
    """latest: nội dung latest.json; text_history: các chỉ số văn bản theo giờ (đã gồm lần chạy này)."""
    t0, errs, parts = time.time(), {}, []
    add = lambda key, name, raw, v, w, note, src="": parts.append({"key": key, "name": name, "raw": raw, "v": round(clamp(v), 1), "w": w, "note": note, "src": src})  # noqa: E731
    M: dict = {"errors": errs}

    a = latest.get("all") or {}
    if a.get("index") is not None:
        hs = [h for h in text_history if h is not None]
        rel = pct_rank(hs, a["index"]) if len(hs) >= 24 else None
        add("text", "Giọng điệu tin tức và mạng xã hội", f"{a.get('score', 0):+.2f} ({a.get('n', 0)} bài)",
            (rel + a["index"]) / 2 if rel is not None else a["index"], 25, "so với lịch sử của máy chủ" if rel is not None else "điểm tuyệt đối", "máy chủ")

    f = try_(errs, "fng", fng, 10)
    if f:
        M["fng"] = {"value": f[-1][1], "cls": f[-1][2], "d7": f[-1][1] - f[-8][1] if len(f) >= 8 else None}
        add("fng", "Fear & Greed", str(f[-1][1]), f[-1][1], 20, f[-1][2], "alternative.me")

    vu = try_(errs, "votes", votes)
    if vu is not None:
        M["votes"] = vu
        add("votes", "Phiếu cộng đồng CoinGecko", f"{vu:.1f}% tăng", (vu - 40) * 2.5, 10, "40% → 0, 80% → 100", "CoinGecko")

    fund, fsrc, ferr = None, None, []
    for name, fn in (("Bybit", funding_bybit), ("OKX", funding_okx), ("Hyperliquid", funding_hyperliquid)):
        try:
            s = sorted(set(fn()))
            s = [x for x in s if x[0] >= (time.time() - 90 * 86400) * 1000]
            if len(s) >= 30:
                fund, fsrc = s, name
                break
            ferr.append(f"{name}: ít dữ liệu")
        except Exception as ex:  # noqa: BLE001
            ferr.append(f"{name}: {str(ex)[:60]}")
    if fund:
        vals = [v for _, v in fund]
        pr = pct_rank(vals, vals[-1])
        M["funding"] = {"src": fsrc, "last": vals[-1], "pct": round(pr, 1), "n": len(vals)}
        add("funding", "Funding BTC", f"{vals[-1] * 100:+.4f}%", pr, 20, f"phân vị trong 90 ngày ({fsrc})", fsrc)
    else:
        errs["funding"] = "; ".join(ferr)[:200]

    ls = try_(errs, "ls", long_short)
    if ls:
        M["ls"] = {"buy": round(ls[0], 4), "src": ls[1]}
        add("ls", "Tài khoản đang long", f"{ls[0] * 100:.0f}%", (ls[0] - 0.4) / 0.35 * 100, 10, f"40% → 0, 75% → 100 ({ls[1]})", ls[1])

    pc = try_(errs, "pc", put_call)
    if pc is not None:
        M["pc"] = round(pc, 3)
        add("pc", "Put/call quyền chọn", f"{pc:.2f}", (1 - pc) / 0.6 * 100, 10, "càng thấp càng chủ quan", "Deribit")

    wz = try_(errs, "wiki", wiki_z)
    if wz:
        M["wiki"] = {"z": round(wz[0], 2), "last": wz[1]}
        add("wiki", "Chú ý Wikipedia", f"{wz[0]:+.1f} độ lệch chuẩn", 50 + 25 * wz[0], 15, "so với 60 ngày", "Wikipedia")

    W = sum(p["w"] for p in parts)
    value = round(sum(p["v"] * p["w"] for p in parts) / W) if W else None
    M["composite"] = {"value": value, "parts": parts, "weight": W}

    # Coinbase Premium: Coinbase (người Mỹ, tổ chức) trả giá cao hơn sàn quốc tế bao nhiêu
    cb = try_(errs, "coinbase", coinbase_hourly)
    ref = try_(errs, "usdt_ref", usdt_hourly)
    if cb and ref:
        common = sorted(set(cb) & set(ref[0]))[-168:]
        ser = [(t, (cb[t] / ref[0][t] - 1) * 100) for t in common if ref[0][t]]
        if len(ser) >= 24:
            last24 = [v for _, v in ser[-24:]]
            M["premium"] = {"last": round(ser[-1][1], 4), "avg24": round(sum(last24) / 24, 4), "ref": ref[1],
                            "avg7d": round(sum(v for _, v in ser) / len(ser), 4), "series": [[t * 1000, round(v, 4)] for t, v in ser]}
        M["btc"] = {"price": cb[max(cb)], "ch24": round((cb[max(cb)] / cb[max(cb) - 86400] - 1) * 100, 2) if max(cb) - 86400 in cb else None}

    st = try_(errs, "stable", stablecoins)
    if st:
        last = st[-1]
        def ago(days):
            v = [x for x in st if x[0] <= last[0] - days * 86400]
            return v[-1][1] if v else None
        s7, s30 = ago(7), ago(30)
        M["stable"] = {"total": round(last[1] / 1e9, 1), "ch7": round((last[1] / s7 - 1) * 100, 2) if s7 else None,
                       "ch30": round((last[1] / s30 - 1) * 100, 2) if s30 else None,
                       "d30": round((last[1] - s30) / 1e9, 1) if s30 else None,
                       "series": [[t * 1000, round(v / 1e9, 1)] for t, v in st[-180:]]}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bt = try_(errs, "backtest", backtest, today)
    if bt:
        M["backtest"] = bt

    stamp = latest.get("generated_at") or datetime.now(timezone.utc).isoformat()
    row = {"time_utc": stamp[:19] + "Z", "value": value if value is not None else "", "n_parts": len(parts),
           "btc": M.get("btc", {}).get("price", ""), "premium24": (M.get("premium") or {}).get("avg24", ""), "stable_bn": (M.get("stable") or {}).get("total", "")}
    for p in parts:
        row[p["key"]] = p["v"]
    new = not os.path.exists(COMPOSITE_FILE)
    with open(COMPOSITE_FILE, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)
    M["runtime_sec"] = round(time.time() - t0, 1)
    print(f"Chỉ số tổng hợp: {value} từ {len(parts)} thành phần, lỗi: {list(errs)}, {M['runtime_sec']} giây")
    return M
