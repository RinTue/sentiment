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
    mk = sorted([m for m in mk if rank(m)[0] < 99], key=rank)[:12]
    c = {"saved": time.time(), "symbols": [m["symbol"] for m in mk], "ex_of": {m["symbol"]: ex.get(m["exchange"], m["exchange"]) for m in mk}, "exchanges": sorted({ex.get(m["exchange"], m["exchange"]) for m in mk}, key=lambda n: next((i for i, k in enumerate(CZ_PRIORITY) if k in n.lower()), 99))}
    with open(COINALYZE_CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    return c


def coinalyze(cb: dict | None) -> dict:
    """Thanh lý và open interest gộp nhiều sàn (cần khóa COINALYZE_API_KEY miễn phí)."""
    mk = cz_markets()
    syms = ",".join(mk["symbols"])
    now = int(time.time())
    liq = cz_get("liquidation-history", {"symbols": syms, "interval": "1hour", "from": now - 25 * 3600, "to": now, "convert_to_usd": "true"})
    agg: dict = {}
    for row in liq:
        for h in row.get("history", []):
            a = agg.setdefault(int(h["t"]), [0.0, 0.0])
            a[0] += float(h.get("l") or 0)
            a[1] += float(h.get("s") or 0)
    series = [[t * 1000, round(v[0]), round(v[1])] for t, v in sorted(agg.items()) if t >= (now - now % 3600) - 23 * 3600]
    out = {"exchanges": mk["exchanges"], "symbols": len(mk["symbols"]), "series": series,
           "h24": {"long": round(sum(x[1] for x in series)), "short": round(sum(x[2] for x in series))}}
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
