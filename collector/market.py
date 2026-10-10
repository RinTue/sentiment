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


# ---------------------------------------------------------------- đà giá đến từ đâu
DRIVERS_FILE = os.path.join(DATA, "drivers.csv")


def okx_rows(path: str, params: dict) -> list:
    j = get("https://www.okx.com" + path, params, 12)
    if j.get("code") != "0":
        raise ValueError("OKX: " + str(j.get("msg")))
    return j["data"]


def okx_oi_hourly() -> list:
    """Open interest của hợp đồng BTC-USDT-SWAP trên OKX theo giờ, tính bằng BTC (không bị giá làm méo)."""
    rows = okx_rows("/api/v5/rubik/stat/contracts/open-interest-history", {"instId": "BTC-USDT-SWAP", "period": "1H", "limit": 100})
    return sorted((int(r[0]) // 1000, float(r[2])) for r in rows)


def okx_taker_hourly(inst_type: str) -> list:
    """Khối lượng mua/bán chủ động theo giờ: SPOT tính bằng BTC, CONTRACTS tính bằng USD."""
    begin = int((time.time() - 80 * 3600) * 1000)
    rows = okx_rows("/api/v5/rubik/stat/taker-volume", {"ccy": "BTC", "instType": inst_type, "period": "1H", "begin": begin})
    return sorted((int(r[0]) // 1000, float(r[1]), float(r[2])) for r in rows)   # (giờ, bán, mua)


def okx_liquidations(hours: int = 24, max_pages: int = 30) -> dict:
    """Lệnh bị thanh lý trên OKX (BTC-USDT-SWAP, mỗi hợp đồng 0,01 BTC). Chỉ là một sàn, dùng để thấy phe nào bị ép."""
    cutoff = (time.time() - hours * 3600) * 1000
    long_usd = short_usd = 0.0
    oldest, after = None, None
    hourly: dict = {}
    for _ in range(max_pages):
        p = {"instType": "SWAP", "uly": "BTC-USDT", "state": "filled", "limit": 100}
        if after:
            p["after"] = after
        data = okx_rows("/api/v5/public/liquidation-orders", p)
        det = [d for x in data for d in x.get("details", []) if (x.get("instId") or "BTC-USDT-SWAP") == "BTC-USDT-SWAP"]
        if not det:
            break
        for d in det:
            t = float(d["ts"])
            if t < cutoff:
                continue
            usd = float(d["sz"]) * 0.01 * float(d["bkPx"])
            hb = hourly.setdefault(int(t // 3600000) * 3600000, [0.0, 0.0])
            if (d.get("posSide") or ("short" if d.get("side") == "buy" else "long")) == "short":
                short_usd += usd
                hb[1] += usd
            else:
                long_usd += usd
                hb[0] += usd
        oldest = min(float(d["ts"]) for d in det)
        if oldest < cutoff:
            break
        after = int(oldest)
        time.sleep(0.15)
    covered = min(hours, (time.time() * 1000 - (oldest or time.time() * 1000)) / 3600e3)
    return {"long": round(long_usd), "short": round(short_usd), "hours": round(covered, 1),
            "series": [[t, round(v[0]), round(v[1])] for t, v in sorted(hourly.items())]}


COINALYZE_KEY = os.environ.get("COINALYZE_API_KEY", "").strip()
COINALYZE_CACHE = os.path.join(DATA, "coinalyze_markets.json")
CZ_PRIORITY = ["binance", "bybit", "okx", "bitget", "hyperliquid", "deribit", "bitmex", "gate", "htx", "huobi", "kraken", "bitfinex", "dydx", "coinbase", "bingx", "mexc"]


def cz_get(path: str, params: dict | None = None):
    r = requests.get("https://api.coinalyze.net/v1/" + path, params=params, headers={"api_key": COINALYZE_KEY, "User-Agent": UA}, timeout=20)
    if r.status_code == 429:
        time.sleep(min(30, float(r.headers.get("Retry-After", 10))))
        r = requests.get("https://api.coinalyze.net/v1/" + path, params=params, headers={"api_key": COINALYZE_KEY, "User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()


def cz_markets() -> dict:
    """Danh sách hợp đồng vĩnh cửu BTC trên các sàn lớn (lưu 3 ngày để tiết kiệm lượt gọi)."""
    try:
        with open(COINALYZE_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        if time.time() - c.get("saved", 0) < 3 * 86400 and c.get("symbols") and c.get("ex_of"):
            return c
    except (OSError, json.JSONDecodeError):
        pass
    ex = {e["code"]: e["name"] for e in cz_get("exchanges")}
    mk = [m for m in cz_get("future-markets") if (m.get("base_asset") or "").upper() == "BTC" and m.get("is_perpetual")]
    def rank(m):
        name = ex.get(m.get("exchange"), "").lower()
        pr = next((i for i, k in enumerate(CZ_PRIORITY) if k in name), 99)
        q = (m.get("quote_asset") or "").upper()
        return (pr, 0 if q == "USDT" else 1 if q == "USD" else 2)
    per_ex: dict = {}
    picked = []
    for m in sorted([m for m in mk if rank(m)[0] < 99], key=rank):   # tối đa 2 hợp đồng mỗi sàn để phủ được nhiều sàn
        if per_ex.get(m["exchange"], 0) < 2:
            per_ex[m["exchange"]] = per_ex.get(m["exchange"], 0) + 1
            picked.append(m)
    mk = picked[:12]
    c = {"saved": time.time(), "symbols": [m["symbol"] for m in mk], "ex_of": {m["symbol"]: ex.get(m["exchange"], m["exchange"]) for m in mk}, "exchanges": sorted({ex.get(m["exchange"], m["exchange"]) for m in mk}, key=lambda n: next((i for i, k in enumerate(CZ_PRIORITY) if k in n.lower()), 99))}
    with open(COINALYZE_CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    return c


def coinalyze(cb: dict | None) -> dict:
    """Thanh lý và open interest gộp nhiều sàn (cần khóa COINALYZE_API_KEY miễn phí)."""
    mk = cz_markets()
    syms = ",".join(mk["symbols"])
    now = int(time.time())
    liq = cz_get("liquidation-history", {"symbols": syms, "interval": "1hour", "from": now - 73 * 3600, "to": now, "convert_to_usd": "true"})
    agg: dict = {}
    for row in liq:
        for h in row.get("history", []):
            a = agg.setdefault(int(h["t"]), [0.0, 0.0])
            a[0] += float(h.get("l") or 0)
            a[1] += float(h.get("s") or 0)
    series = [[t * 1000, round(v[0]), round(v[1])] for t, v in sorted(agg.items()) if t >= (now - now % 3600) - 23 * 3600]
    s72 = [v for t, v in agg.items() if t >= (now - now % 3600) - 71 * 3600]
    out = {"exchanges": mk["exchanges"], "symbols": len(mk["symbols"]), "series": series,
           "h24": {"long": round(sum(x[1] for x in series)), "short": round(sum(x[2] for x in series))},
           "h72": {"long": round(sum(v[0] for v in s72)), "short": round(sum(v[1] for v in s72))} if len(s72) >= 60 else None}
    oi = cz_get("open-interest-history", {"symbols": syms, "interval": "1hour", "from": now - 170 * 3600, "to": now, "convert_to_usd": "true"})
    tot: dict = {}
    cnt: dict = {}
    last_oi: dict = {}
    for row in oi:
        hist = row.get("history", [])
        if hist:
            last_oi[row.get("symbol")] = float(hist[-1].get("c") or 0)
        for h in hist:
            tot[int(h["t"])] = tot.get(int(h["t"]), 0.0) + float(h.get("c") or 0)
            cnt[int(h["t"])] = cnt.get(int(h["t"]), 0) + 1
    full = max(cnt.values()) if cnt else 0
    comp = sorted(t for t in tot if cnt[t] == full)
    if comp:
        o_now = tot[comp[-1]]
        o_7 = next((tot[t] for t in comp if t >= comp[-1] - 7 * 86400), None)
        out["oi"] = {"usd": round(o_now), "ch7": round((o_now / o_7 - 1) * 100, 2) if o_7 else None,
                     "series": [[t * 1000, round(tot[t] / 1e9, 3)] for t in comp[::4]]}
    # Funding gộp: bình quân theo open interest của từng hợp đồng; bỏ sàn tính funding mỗi giờ để cùng thang 8 giờ.
    try:
        fr = cz_get("funding-rate-history", {"symbols": syms, "interval": "daily", "from": now - 92 * 86400, "to": now})
        ex_of = mk.get("ex_of", {})
        num_: dict = {}
        den_: dict = {}
        raw = []
        for row in fr:
            sym = row.get("symbol")
            if any(k in (ex_of.get(sym, "") or "").lower() for k in ("hyperliquid", "dydx")):
                continue
            w = last_oi.get(sym) or 0
            if w <= 0:
                continue
            for h in row.get("history", []):
                v = float(h.get("c") if h.get("c") is not None else h.get("v", 0))
                raw.append(abs(v))
                num_[int(h["t"])] = num_.get(int(h["t"]), 0.0) + v * w
                den_[int(h["t"])] = den_.get(int(h["t"]), 0.0) + w
        if num_:
            scale = 0.01 if raw and sorted(raw)[len(raw) // 2] > 0.003 else 1.0   # Coinalyze có thể trả theo %
            ser = [(t, num_[t] / den_[t] * scale) for t in sorted(num_) if den_[t] > 0]
            vals = [v for _, v in ser]
            if len(vals) >= 20:
                out["funding"] = {"last": vals[-1], "pct": round(pct_rank(vals, vals[-1]), 1), "n": len(vals),
                                  "series": [[t * 1000, v] for t, v in ser]}
    except Exception as ex:  # noqa: BLE001
        out["funding_error"] = str(ex)[:120]
    if cb and full:
        ser = []
        for t in sorted(tot):
            if cnt[t] < full:      # bỏ giờ thiếu sàn để chuỗi không nhảy bậc
                continue
            px = cb.get(t) or cb.get(t - t % 3600)
            if px:
                ser.append((t, tot[t] / px))
        if len(ser) >= 30:
            out["oi_btc"] = ser
    return out


def classify_move(dp: float, doi: float, fpct: float | None, spot_net: float | None, perp_net: float | None, prem: float | None,
                  liq: dict | None, thr_p: float, thr_oi: float) -> tuple[str, str, str]:
    """Trả về (mã, nhãn, giải thích). Đọc theo thứ tự: open interest (vị thế đóng hay mở), thanh lý (phe nào bị ép),
    funding (đòn bẩy), lệnh mua bán chủ động futures so với spot, rồi Coinbase Premium (người mua Mỹ)."""
    sp = 0
    if spot_net is not None:
        sp += 1 if spot_net > 0.02 else -1 if spot_net < -0.02 else 0
    if prem is not None:
        sp += 1 if prem > 0.03 else -1 if prem < -0.03 else 0
    okx_txt = "chưa có số liệu" if spot_net is None else "được mua ròng" if spot_net > 0.02 else "bị bán ròng" if spot_net < -0.02 else "gần như cân bằng"
    us_txt = "" if prem is None else "đang mua mạnh hơn (Coinbase Premium dương)" if prem > 0.03 else "đang bán (Coinbase Premium âm)" if prem < -0.03 else "cân bằng"
    spot_txt = f" Spot: trên OKX {okx_txt}" + (f", phía Mỹ {us_txt}" if us_txt else "") + "."
    lq_long, lq_short = (liq or {}).get("long", 0) or 0, (liq or {}).get("short", 0) or 0
    pn, sn = perp_net or 0, spot_net or 0
    if abs(dp) < thr_p:
        if doi > thr_oi * 1.5:
            return "build", "Đòn bẩy đang tích tụ", "Giá đi ngang nhưng open interest tăng: vị thế mới đang dồn vào, dễ có cú quét mạnh ở một trong hai phía."
        return "flat", "Chưa có đà rõ", "Giá và open interest đều ít thay đổi."
    if dp > 0:
        if doi <= -thr_oi:
            return "squeeze_up", "Short bị ép đóng (short squeeze)", "Giá tăng trong khi open interest giảm: phần lớn lực mua đến từ phe short phải đóng lệnh." + spot_txt
        if lq_short >= 10e6 and lq_short >= 3 * lq_long:
            return "liq_up", "Short bị thanh lý dây chuyền", "Short bị thanh lý gấp nhiều lần long, đẩy giá lên nhanh; open interest chưa giảm nhiều vì có vị thế mới mở thêm." + spot_txt
        if doi >= thr_oi and (fpct or 0) >= 70:
            return "lev_up", "Long đòn bẩy dẫn dắt", "Giá, open interest và funding cùng tăng: người mua chủ yếu dùng đòn bẩy, dễ bị đảo chiều và thanh lý dây chuyền." + spot_txt
        if pn > 0.015 and pn > sn + 0.01 and sp <= 0:
            return "perp_up", "Mua futures dẫn dắt", "Lệnh mua chủ động tập trung ở futures, spot không theo kịp: đà tăng thiếu nền tiền thật." + spot_txt
        if sp >= 1:
            return "spot_up", "Mua spot dẫn dắt", "Giá tăng mà đòn bẩy không tăng tương ứng, trong khi spot được mua ròng: đà tăng lành mạnh hơn."
        return "mixed_up", "Tăng do nhiều lực cùng lúc", "Không có dấu hiệu nào áp đảo giữa đóng short, long đòn bẩy và mua spot." + spot_txt
    if doi <= -thr_oi:
        return "squeeze_dn", "Long bị thanh lý (xả đòn bẩy)", "Giá giảm trong khi open interest giảm: phần lớn lực bán đến từ long bị thanh lý hoặc tự đóng lệnh." + spot_txt
    if lq_long >= 10e6 and lq_long >= 3 * lq_short:
        return ("liq_dn", "Long bị thanh lý" + (", short mới vào" if doi >= 0 else ""),
                "Long bị thanh lý gấp nhiều lần short, kéo giá xuống" + ("; open interest không giảm vì phe short mở thêm vị thế." if doi >= 0 else ".") + spot_txt)
    if doi >= thr_oi and (fpct if fpct is not None else 50) <= 30:
        return "lev_dn", "Short đòn bẩy dẫn dắt", "Giá giảm trong khi open interest tăng và funding thấp: short mới đang dồn vào; nếu giá bật lên, dễ thành short squeeze." + spot_txt
    if pn < -0.015 and pn < sn - 0.01 and sp >= 0:
        return "perp_dn", "Bán futures dẫn dắt", "Lệnh bán chủ động tập trung ở futures trong khi spot không bị bán mạnh: đợt giảm chủ yếu do giới đầu cơ." + spot_txt
    if sp <= -1:
        return "spot_dn", "Bán spot dẫn dắt", "Giá giảm cùng lực bán spot" + (" và Coinbase Premium âm" if prem is not None and prem < -0.03 else "") + ": người bán thật đang rút tiền, đáng lo hơn xả đòn bẩy."
    return "mixed_dn", "Giảm do nhiều lực cùng lúc", "Không có dấu hiệu nào áp đảo giữa thanh lý long, short mới và bán spot." + spot_txt


# ---------------------------------------------------------------- kết luận tự động cho "Đà giá đến từ đâu"
def _f1(x: float, sign: bool = False) -> str:
    return (f"{x:+.1f}" if sign else f"{x:.1f}").replace(".", ",")


def _usd(v: float) -> str:
    v = abs(v)
    return f"{_f1(v / 1e9)} tỷ $" if v >= 1e9 else f"{_f1(v / 1e6)} triệu $" if v >= 1e6 else f"{round(v / 1e3)} nghìn $"


def _btc(v: float) -> str:
    v = abs(v)
    return f"{round(v):,}".replace(",", ".") if v >= 100 else _f1(v)


def _px(v: float) -> str:
    return "$" + f"{round(v):,}".replace(",", ".")


def analyze_move(w: dict, hours: int, liq: dict | None, fpct: float | None, prices: list) -> dict:
    """Đọc các lực mua/bán của một khung thời gian rồi đưa ra một kết luận cụ thể: nguyên nhân chính, độ bền của đà, điều cần theo dõi.
    w: một khung của drivers() (spot_net/perp_net tính bằng % khối lượng, prem bằng %)."""
    dp, doi = w["dp"], w["doi"]
    sn, pn, prem = w.get("spot_net"), w.get("perp_net"), w.get("prem")
    small, thr = (1.0, 2.0) if hours == 24 else (2.0, 4.0)       # ngưỡng giá: dưới small là đi ngang
    thr_oi = 1.5 if hours == 24 else 2.5
    win = "24 giờ" if hours == 24 else "3 ngày"
    forces = []      # (tên, chiều: +1 đẩy giá lên / -1 kéo xuống / 0, mô tả)

    s_side = 0
    if sn is not None:
        s_side = 1 if sn >= 1 else -1 if sn <= -1 else 0
        b = w.get("spot_btc")
        forces.append(("Spot", s_side, ("mua ròng" if s_side > 0 else "bán ròng" if s_side < 0 else "cân bằng") + (f" {_btc(b)} BTC trên OKX" if b and s_side else "") + f" ({_f1(sn, True)}% khối lượng)"))
    p_side = 0
    if prem is not None:
        p_side = 1 if prem >= 0.03 else -1 if prem <= -0.03 else 0
        forces.append(("Người mua Mỹ", p_side, ("mua mạnh hơn" if p_side > 0 else "bán, đứng ngoài" if p_side < 0 else "trung tính") + " (Coinbase Premium " + f"{prem:+.2f}".replace(".", ",") + "%)"))
    f_side = 0
    if pn is not None:
        f_side = 1 if pn >= 1.5 else -1 if pn <= -1.5 else 0
        u = w.get("perp_usd")
        forces.append(("Futures", f_side, ("mua chủ động" if f_side > 0 else "bán chủ động" if f_side < 0 else "cân bằng") + (f" {_usd(u)} ròng" if u and f_side else "") + f" ({_f1(pn, True)}%)"))
    l_side, L, S = 0, 0.0, 0.0
    if liq:
        L, S = float(liq.get("long") or 0), float(liq.get("short") or 0)
        floor = 3e6 if hours == 24 else 6e6
        if S >= 2 * L and S >= floor:
            l_side = 1
        elif L >= 2 * S and L >= floor:
            l_side = -1
        ratio = (S / L if l_side > 0 else L / S if l_side < 0 and S else 0) if (L and S) else 0
        txt = ("short bị thanh lý" if l_side > 0 else "long bị thanh lý" if l_side < 0 else "không phía nào áp đảo") + f": long {_usd(L)}, short {_usd(S)}"
        if ratio >= 2:
            txt += f" (gấp {_f1(ratio)} lần)"
        forces.append(("Thanh lý", l_side, txt))
    o_side = 1 if doi >= thr_oi else -1 if doi <= -thr_oi else 0
    forces.append(("Open interest", 0, ("vị thế mới mở thêm" if o_side > 0 else "vị thế đang đóng bớt" if o_side < 0 else "ít đổi") + f" ({_f1(doi, True)}%)"))
    if fpct is not None:
        forces.append(("Funding", 0, ("phe long trả phí cao" if fpct >= 80 else "phe short đông" if fpct <= 20 else "bình thường") + f" (phân vị {round(fpct)} trên 90 ngày)"))

    rm = s_side + p_side          # tiền thật: spot toàn cầu + người mua Mỹ
    up, down = dp >= small, dp <= -small
    mag = "mạnh" if abs(dp) >= 2 * thr else "" if abs(dp) >= thr else "nhẹ"
    move = f"{'Tăng' if dp > 0 else 'Giảm'}{' ' + mag if mag else ''} {_f1(abs(dp))}%"
    causes = []   # (độ mạnh, câu ngắn, câu đầy đủ)
    if up:
        if rm >= 1:
            causes.append((2 + rm, "tiền thật mua vào", "tiền thật mua vào (" + ", ".join(x for x in ["spot mua ròng" if s_side > 0 else "", "Premium dương" if p_side > 0 else ""] if x) + ")"))
        if o_side < 0:
            causes.append((2.5, "short bị ép đóng", f"short đóng lệnh hoặc bị ép (open interest {_f1(doi, True)}%)"))
        if l_side > 0:
            causes.append((2, "short bị thanh lý", f"short bị thanh lý {_usd(S)}"))
        if o_side > 0:
            causes.append((2.2 if (fpct or 0) >= 70 else 1.5, "long đòn bẩy", f"long đòn bẩy mở thêm (open interest {_f1(doi, True)}%{', funding cao' if (fpct or 0) >= 70 else ''})"))
        if f_side > 0 and rm <= 0:
            causes.append((1.8, "mua futures", "lệnh mua chủ động dồn ở futures"))
    elif down:
        if rm <= -1:
            causes.append((2 - rm, "bán thật", "bán thật (" + ", ".join(x for x in ["spot bán ròng" if s_side < 0 else "", "Premium âm" if p_side < 0 else ""] if x) + ")"))
        if f_side < 0:
            causes.append((1.8, "bán futures", "bán chủ động trên futures"))
        if l_side < 0:
            causes.append((2, "long bị thanh lý", f"long bị thanh lý {_usd(L)}" + (f", gấp {_f1(L / S)} lần short" if S and L / S >= 2 else "")))
        if o_side < 0:
            causes.append((2.5, "xả đòn bẩy", f"long đóng lệnh, xả đòn bẩy (open interest {_f1(doi, True)}%)"))
        if o_side > 0:
            causes.append((2.2 if (fpct if fpct is not None else 50) <= 30 else 1.5, "short mới dồn vào", f"short mới mở thêm (open interest {_f1(doi, True)}%)"))
    causes.sort(key=lambda c: -c[0])

    # Độ bền của đà và giọng kết luận
    if up:
        if rm >= 1 and o_side <= 0:
            tone, ass = "pos", "Đà tăng có tiền thật đỡ phía sau và đòn bẩy chưa nóng: loại đà tăng bền hơn."
        elif rm >= 1:
            tone, ass = "warn", "Có tiền thật mua, nhưng đòn bẩy cũng đang tăng theo" + (" và funding cao" if (fpct or 0) >= 70 else "") + ": đà còn tốt, cẩn thận nếu đòn bẩy tăng nhanh hơn spot."
        elif o_side < 0 or l_side > 0:
            tone, ass = "warn", "Đà tăng chủ yếu nhờ short bị ép, tiền thật chưa theo. Khi short đã đóng xong mà spot không mua tiếp, đà thường hụt hơi."
        elif o_side > 0:
            tone, ass = "neg", "Tăng bằng đòn bẩy, thiếu tiền thật: dễ bị đảo chiều và quét long."
        else:
            tone, ass = "neutral", "Chưa thấy lực nào áp đảo đứng sau đà tăng."
    elif down:
        if rm <= -1 and o_side > 0:
            tone, ass = "neg", "Có người bán thật, cộng thêm short mới dồn vào: áp lực giảm còn nặng. Nhưng short càng đông thì càng dễ bật ngược mạnh nếu giá giữ được."
        elif rm <= -1:
            tone, ass = "neg", "Có người bán thật, không chỉ là xả đòn bẩy: loại giảm đáng lo hơn và thường cần thời gian để tạo đáy."
        elif (o_side < 0 or l_side < 0) and rm >= 0:
            tone, ass = "pos", "Chủ yếu là xả đòn bẩy, tiền thật không bán theo: kiểu giảm này thường làm sạch thị trường và hay ở gần đáy ngắn hạn."
        elif o_side > 0 and (fpct if fpct is not None else 50) <= 30:
            tone, ass = "warn", "Short đòn bẩy đang dồn vào khi funding thấp: nếu giá không giảm thêm, dễ thành short squeeze."
        else:
            tone, ass = "neutral", "Chưa thấy lực nào áp đảo đứng sau đà giảm."
    else:
        if doi >= 1.5 * thr_oi:
            tone, ass = "warn", f"Giá đứng yên nhưng open interest tăng {_f1(doi)}%: đòn bẩy đang tích tụ, sắp có cú quét mạnh về một phía."
        elif rm + f_side + l_side <= -3:
            tone, ass = "warn", "Lực bán chủ động áp đảo (spot, futures, thanh lý long) mà giá không giảm: đang có bên mua đặt lệnh chờ đỡ giá. Lực đỡ này mất thì giá dễ rơi nhanh; giữ được thì phe bán sẽ đuối."
        elif rm + f_side + l_side >= 3:
            tone, ass = "warn", "Lực mua chủ động áp đảo mà giá không tăng: đang có bên bán đặt lệnh chờ chặn giá. Vượt qua được thì giá dễ bật nhanh; không qua được thì phe mua sẽ đuối."
        elif rm <= -1:
            tone, ass = "neutral", "Giá đứng yên nhưng tiền thật đang nghiêng về bán; chưa đủ để thành xu hướng."
        elif rm >= 1:
            tone, ass = "neutral", "Giá đứng yên nhưng tiền thật đang nghiêng về mua; chưa đủ để thành xu hướng."
        else:
            tone, ass = "neutral", "Thị trường đang chờ: không phe nào chiếm ưu thế."

    if up or down:
        grp = {"short bị ép đóng": 1, "short bị thanh lý": 1, "xả đòn bẩy": 2, "long bị thanh lý": 2}
        second = next((c for c in causes[1:] if not (grp.get(c[1]) and grp.get(c[1]) == grp.get(causes[0][1]))), None) if causes else None
        title = move + (": " + causes[0][1] + (" + " + second[1] if second else "") if causes else "")
        verdict = f"{win} qua BTC {move.lower()}" + (", chủ yếu do " + "; ".join(c[2] for c in causes[:3]) if causes else "") + "."
    else:
        title = f"Đi ngang ({_f1(dp, True)}%)"
        verdict = f"{win} qua BTC gần như đi ngang ({_f1(dp, True)}%)."

    # Mốc giá và điều kiện xác nhận / vô hiệu
    watch = []
    pw = [v for _, v in prices]
    if len(pw) >= 6:
        lo, hi = min(pw), max(pw)
        if down:
            watch.append(f"Thủng {_px(lo)} (đáy {win}) là đà giảm còn tiếp; vượt lại {_px(hi)} (đỉnh {win}) mới coi như phe mua lấy lại thế.")
        elif up:
            watch.append(f"Giữ trên {_px(lo)} (đáy {win}) thì đà tăng còn nguyên; vượt {_px(hi)} là đi tiếp, rơi lại dưới đáy là hỏng.")
        else:
            watch.append(f"Giá đang trong vùng {_px(lo)}–{_px(hi)}; thoát ra khỏi vùng này theo hướng nào thì đà mới rõ.")
    if p_side < 0:
        watch.append("Coinbase Premium quay về dương: dấu hiệu người mua Mỹ trở lại.")
    elif p_side > 0 and up:
        watch.append("Coinbase Premium tụt xuống âm: đà tăng mất chỗ dựa từ người mua Mỹ.")
    if down and o_side > 0:
        watch.append("Open interest tiếp tục tăng mà giá không giảm thêm: short bị kẹt, dễ bật lên mạnh.")
    elif up and o_side > 0 and (fpct or 0) >= 70:
        watch.append("Open interest và funding tăng tiếp khi giá chững lại: rủi ro long bị quét.")
    elif up and rm <= 0:
        watch.append("Spot chuyển sang mua ròng: lúc đó đà tăng mới có nền tiền thật.")
    elif down and s_side < 0:
        watch.append("Spot chuyển sang mua ròng: lực bán thật đã cạn.")
    elif not (up or down) and doi >= 1.5 * thr_oi:
        watch.append("Funding lệch hẳn về một phía cho biết phe nào đang đông hơn, phe đó dễ bị quét.")
    return {"title": title, "verdict": verdict, "tone": tone, "assess": ass,
            "forces": [{"name": n, "side": sd, "text": t} for n, sd, t in forces], "watch": watch[:3]}


def drivers(cb: dict | None, M: dict, errs: dict) -> dict | None:
    agg = M.get("liq_agg") or {}
    oi = agg.get("oi_btc") or try_(errs, "oi_okx", okx_oi_hourly)
    oi_src = f"{len(agg.get('exchanges', []))} sàn (Coinalyze)" if agg.get("oi_btc") else "OKX"
    if not oi or len(oi) < 30:
        return None
    spot = try_(errs, "taker_spot", okx_taker_hourly, "SPOT") or []
    perp = try_(errs, "taker_perp", okx_taker_hourly, "CONTRACTS") or []
    liq = try_(errs, "liq_okx", okx_liquidations, 24)
    liq_cls = {"long": agg["h24"]["long"], "short": agg["h24"]["short"]} if agg.get("h24") else liq
    price = cb or {}
    if not price:
        return None
    t_end = max(price)
    fpct = (M.get("funding") or {}).get("pct")
    prem_series = (M.get("premium") or {}).get("series") or []
    out = {"windows": {}, "series": {}}
    for hours, thr_p, thr_oi in ((24, 2.0, 2.0), (72, 4.0, 4.0)):
        t0 = t_end - hours * 3600
        p_now, p_then = price[t_end], next((price[t] for t in sorted(price) if t >= t0), None)
        oi_then = next((v for t, v in oi if t >= t0), None)
        if not p_then or not oi_then:
            continue
        dp = (p_now / p_then - 1) * 100
        doi = (oi[-1][1] / oi_then - 1) * 100
        sp = [x for x in spot if x[0] >= t0]
        pp = [x for x in perp if x[0] >= t0]
        spot_net = (sum(b - s for _, s, b in sp) / sum(b + s for _, s, b in sp)) if sp and sum(b + s for _, s, b in sp) else None
        perp_net = (sum(b - s for _, s, b in pp) / sum(b + s for _, s, b in pp)) if pp and sum(b + s for _, s, b in pp) else None
        pr = [v for t, v in prem_series if t >= t0 * 1000]
        prem = sum(pr) / len(pr) if pr else None
        code, label, why = classify_move(dp, doi, fpct, spot_net, perp_net, prem, liq_cls if hours == 24 else None, thr_p, thr_oi)
        out["windows"][str(hours)] = {"dp": round(dp, 2), "doi": round(doi, 2), "spot_net": None if spot_net is None else round(spot_net * 100, 1),
                                     "perp_net": None if perp_net is None else round(perp_net * 100, 1), "spot_btc": round(sum(b - s for _, s, b in sp), 1) if sp else None,
                                     "perp_usd": round(sum(b - s for _, s, b in pp)) if pp else None, "prem": None if prem is None else round(prem, 3),
                                     "code": code, "label": label, "why": why}
    if not out["windows"]:
        return None
    for key, w in out["windows"].items():
        h = int(key)
        lq = liq_cls if h == 24 else (agg.get("h72") if agg.get("h72") else None)
        try:
            w["analysis"] = analyze_move(w, h, lq, fpct, [(t, v) for t, v in sorted(price.items()) if t >= t_end - h * 3600])
        except Exception as ex:  # noqa: BLE001
            errs["drv_analysis"] = str(ex)[:120]
    out["liq"] = liq
    out["liq_src"] = "agg" if agg.get("h24") else "okx"
    out["oi_src"] = oi_src
    out["funding_pct"] = fpct
    # chuỗi theo giờ cho biểu đồ (4 ngày): giá, OI, CVD spot (BTC), CVD futures (USD)
    t_min = t_end - 96 * 3600
    out["series"]["price"] = [[t * 1000, round(v)] for t, v in sorted(price.items()) if t >= t_min]
    out["series"]["oi"] = [[t * 1000, round(v, 1)] for t, v in oi if t >= t_min]
    c = 0.0
    out["series"]["cvd_spot"] = [[t * 1000, round(c := c + (b - s), 1)] for t, s, b in spot if t >= t_min]
    c = 0.0
    out["series"]["cvd_perp"] = [[t * 1000, round((c := c + (b - s)) / 1e6, 1)] for t, s, b in perp if t >= t_min]
    w = out["windows"].get("24") or out["windows"].get("72")
    new = not os.path.exists(DRIVERS_FILE)
    with open(DRIVERS_FILE, "a", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        if new:
            wr.writerow(["time_utc", "price", "oi_btc", "dp24", "doi24", "spot_net24", "perp_net24", "prem24", "code24", "liq_long24", "liq_short24"])
        wr.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), round(price[t_end]), round(oi[-1][1], 1), w["dp"], w["doi"], w["spot_net"],
                     w["perp_net"], w["prem"], w["code"], (liq or {}).get("long", ""), (liq or {}).get("short", "")])
    return out


# ---------------------------------------------------------------- dòng tiền ETF giao ngay (Mỹ)
# API cũ của SoSoValue (api.sosovalue.xyz) đã ngừng. Ưu tiên API mới của SoSoValue (cần khóa miễn phí SOSOVALUE_API_KEY),
# không có khóa hoặc lỗi thì đọc bảng của Farside. Kết quả được lưu lại để một lần lỗi không làm trống biểu đồ.
SOSO_KEY = os.environ.get("SOSOVALUE_API_KEY", "").strip()
ETF_CACHE = os.path.join(DATA, "etf_cache.json")
FARSIDE = {"BTC": "https://farside.co.uk/bitcoin-etf-flow-all-data/", "ETH": "https://farside.co.uk/ethereum-etf-flow-all-data/"}
MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def etf_soso(coin: str) -> list:
    r = requests.get("https://openapi.sosovalue.com/openapi/v1/etfs/summary-history", params={"symbol": coin, "country_code": "US", "limit": 300},
                     headers={"x-soso-api-key": SOSO_KEY, "User-Agent": UA, "Accept": "application/json"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json()
    rows = j.get("data") if isinstance(j, dict) else j
    if isinstance(j, dict) and j.get("code") not in (0, None):
        raise RuntimeError(f"mã {j.get('code')}")
    out = [[d["date"], float(d["total_net_inflow"]), float(d["total_net_assets"]) if d.get("total_net_assets") is not None else None]
           for d in rows or [] if d.get("date") and d.get("total_net_inflow") is not None]
    if not out:
        raise RuntimeError("không có dòng dữ liệu")
    return sorted(out)


def etf_farside(coin: str) -> list:
    import html as _html
    import re
    r = requests.get(FARSIDE[coin], headers={"User-Agent": UA, "Accept": "text/html"}, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S | re.I):
        cells = [_html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) < 3:
            continue
        m = re.fullmatch(r"(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})", cells[0])
        if not m or m.group(2) not in MON:
            continue
        tot = cells[-1].replace(",", "").replace("\xa0", "").strip()
        if tot in ("", "-"):
            continue
        neg = tot.startswith("(") and tot.endswith(")")
        try:
            v = float(tot.strip("()")) * (-1 if neg else 1) * 1e6
        except ValueError:
            continue
        out.append([f"{m.group(3)}-{MON[m.group(2)]:02d}-{int(m.group(1)):02d}", v, None])
    if len(out) < 20:
        raise RuntimeError(f"chỉ đọc được {len(out)} dòng")
    # Farside ghi 0,0 cho ngày đã qua nhưng chưa có số: bỏ các ngày 0 ở cuối bảng
    out = sorted({d: [d, v, a] for d, v, a in out}.values())
    while out and out[-1][1] == 0:
        out.pop()
    return out


def etf_llama(coin: str) -> list:
    """DefiLlama (trang ETF của họ): không cần khóa. Đọc mềm dẻo vì định dạng không có tài liệu chính thức."""
    r = requests.get("https://etfs.llama.fi/flows", headers={"User-Agent": UA, "Accept": "application/json"}, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json()
    rows = j if isinstance(j, list) else next((v for v in j.values() if isinstance(v, list)), []) if isinstance(j, dict) else []
    if not rows or not isinstance(rows[0], dict):
        raise RuntimeError("định dạng lạ: " + (str(list(j.keys()))[:80] if isinstance(j, dict) else type(j).__name__))
    k0 = rows[0].keys()
    dk = next((k for k in ("day", "date", "timestamp") if k in k0), None)
    ck = next((k for k in ("gecko_id", "asset", "coin", "symbol") if k in k0), None)
    fk = next((k for k in k0 if "flow" in k.lower()), None)
    if not (dk and ck and fk):
        raise RuntimeError("thiếu cột: " + ",".join(list(k0))[:90])
    want = {"BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "eth")}[coin]
    agg: dict = {}
    for d in rows:
        if str(d.get(ck, "")).lower() not in want or d.get(fk) is None:
            continue
        day = d[dk]
        day = datetime.fromtimestamp(day if day < 1e11 else day / 1000, timezone.utc).strftime("%Y-%m-%d") if isinstance(day, (int, float)) else str(day)[:10]
        agg[day] = agg.get(day, 0.0) + float(d[fk])
    if len(agg) < 20:
        raise RuntimeError(f"chỉ có {len(agg)} ngày")
    return [[d, v, None] for d, v in sorted(agg.items())]


def etf_flows(errs: dict) -> dict | None:
    try:
        cache = json.load(open(ETF_CACHE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    res = {}
    for coin in ("BTC", "ETH"):
        rows, src = None, None
        # Đã thử (10/2026): Farside chặn máy chủ GitHub (HTTP 403), DefiLlama chuyển ETF sang gói trả phí (HTTP 402).
        # Hai hàm etf_farside/etf_llama giữ lại phòng khi họ mở lại; hiện chỉ dùng SoSoValue.
        if not SOSO_KEY:
            errs[f"etf_soso_{coin}"] = "chưa có khóa SOSOVALUE_API_KEY trong Secrets"
        else:
            try:
                rows, src = etf_soso(coin), "SoSoValue"
            except Exception as ex:  # noqa: BLE001
                errs[f"etf_soso_{coin}"] = str(ex)[:120]
        if rows:
            cache[coin] = {"src": src, "saved": time.time(), "hist": rows[-300:]}
        c = cache.get(coin)
        if c:
            res[coin] = {"src": c["src"], "stale": rows is None, "age_h": round((time.time() - c["saved"]) / 3600, 1), "hist": c["hist"][-120:]}
    with open(ETF_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    return res or None


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

    # Giá theo giờ trên Coinbase và số liệu gộp nhiều sàn (Coinalyze) lấy trước vì phần funding và đà giá cần đến.
    cb = try_(errs, "coinbase", coinbase_hourly)
    ca = try_(errs, "coinalyze", coinalyze, cb) if COINALYZE_KEY else None
    if ca:
        M["liq_agg"] = ca

    fund, fsrc, ferr = None, None, []
    fa = (ca or {}).get("funding")
    if fa:
        n_ex = len(ca.get("exchanges", []))
        M["funding"] = {"src": f"gộp {n_ex} sàn", "last": fa["last"], "pct": fa["pct"], "n": fa["n"], "agg": True}
        add("funding", "Funding BTC (gộp các sàn)", f"{fa['last'] * 100:+.4f}%", fa["pct"], 20, f"phân vị trong 90 ngày, bình quân theo open interest {n_ex} sàn", "Coinalyze")
    for name, fn in (() if fa else (("Bybit", funding_bybit), ("OKX", funding_okx), ("Hyperliquid", funding_hyperliquid))):
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
    elif not fa:
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
    ref = try_(errs, "usdt_ref", usdt_hourly)
    if cb and ref:
        common = sorted(set(cb) & set(ref[0]))[-168:]
        ser = [(t, (cb[t] / ref[0][t] - 1) * 100) for t in common if ref[0][t]]
        if len(ser) >= 24:
            last24 = [v for _, v in ser[-24:]]
            M["premium"] = {"last": round(ser[-1][1], 4), "avg24": round(sum(last24) / 24, 4), "ref": ref[1],
                            "avg7d": round(sum(v for _, v in ser) / len(ser), 4), "series": [[t * 1000, round(v, 4)] for t, v in ser]}
        M["btc"] = {"price": cb[max(cb)], "ch24": round((cb[max(cb)] / cb[max(cb) - 86400] - 1) * 100, 2) if max(cb) - 86400 in cb else None}

    dv = try_(errs, "drivers", drivers, cb, M, errs)
    if M.get("liq_agg"):
        M["liq_agg"].pop("oi_btc", None)   # đã dùng trong phân tích đà giá, không cần ghi vào latest.json
    if dv:
        M["drivers"] = dv

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

    ef = try_(errs, "etf", etf_flows, errs)
    if ef:
        M["etf"] = ef

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
