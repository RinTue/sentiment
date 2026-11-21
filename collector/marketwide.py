"""Toàn cảnh thị trường crypto và lịch crypto tự động.

- Toàn cảnh: tổng vốn hoá, altcoin, tỷ trọng BTC và stablecoin, độ rộng (bao nhiêu trong top 100 đang giảm),
  nhóm ngành. Nguồn: CoinGecko (không cần mã), dự phòng CoinPaprika, dự phòng cuối là 39 altcoin OKX đã lưu.
- Lịch crypto: các kỳ đáo hạn quyền chọn BTC/ETH trên Deribit (giá trị, put/call, max pain), ngày hết hạn
  hợp đồng tương lai BTC của CME (thứ Sáu cuối tháng), cùng sự kiện nhập tay còn hiệu lực.
Ghi data/marketwide.json; trả về bản tóm tắt cho latest.json. Lỗi nguồn nào thì giữ bản đã lưu của phần đó.
"""
from __future__ import annotations

import json
import os
import statistics as stx
import time
from datetime import date, datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "marketwide.json")
UA = {"User-Agent": "sentiment-dashboard (github.com/RinTue/sentiment)", "Accept": "application/json"}
REFRESH = 50 * 60

STABLE = {"usdt", "usdc", "dai", "fdusd", "usde", "usds", "pyusd", "tusd", "usd1", "busd", "frax", "rlusd", "usdtb", "usdd", "gusd",
          "usdp", "lusd", "eurc", "eurt", "usdy", "usdx", "bfusd", "usdf", "usdg", "usd0", "susd", "crvusd", "gho", "ustc", "usdb", "usr", "dola"}
WRAPPED = {"wbtc", "weth", "steth", "wsteth", "weeth", "cbbtc", "reth", "cbeth", "jitosol", "msol", "bnsol", "lbtc", "solvbtc", "tbtc", "ezeth",
           "rseth", "meth", "wbeth", "susde", "sdai", "wbnb", "btcb", "bbtc", "fbtc", "unibtc", "pumpbtc", "clbtc", "xaut", "paxg", "lseth", "oseth",
           "wsol", "hbtc", "renbtc", "jupsol", "bsol", "stx-wrapped", "weeth"}
SECTOR = {
    "Layer 1": "eth sol ada avax sui near apt dot atom sei icp algo trx tia hbar xlm ton kas egld ftm s hype xtz mina flow kda inj",
    "DeFi": "uni aave crv ldo ena mkr sky comp snx pendle jup ray cake 1inch rune dydx gmx morpho ethfi aero",
    "Meme": "doge pepe shib bonk wif floki trump pengu fartcoin spx brett popcat mog",
    "Layer 2": "arb op pol matic imx stx mnt zk strk metis base",
    "AI & dữ liệu": "tao fet render rndr wld grt virtual ai16z ar fil akt ocean io",
    "RWA": "ondo om cfg plume syrup",
    "Thanh toán & coin cũ": "xrp ltc bch etc bnb link xmr zec dash bsv",
}
SYM2SEC = {s: k for k, v in SECTOR.items() for s in v.split()}


def _get(url, params=None, timeout=30):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def _load() -> dict:
    try:
        return json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def coins_coingecko() -> tuple[list, dict]:
    rows = _get("https://api.coingecko.com/api/v3/coins/markets",
                {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 200, "page": 1, "price_change_percentage": "24h,7d,30d"})
    out = [{"sym": (r.get("symbol") or "").lower(), "name": r.get("name") or "", "cap": r.get("market_cap") or 0, "price": r.get("current_price"),
            "c1": r.get("price_change_percentage_24h_in_currency"), "c7": r.get("price_change_percentage_7d_in_currency"),
            "c30": r.get("price_change_percentage_30d_in_currency")} for r in rows]
    g = _get("https://api.coingecko.com/api/v3/global").get("data") or {}
    glob = {"total": (g.get("total_market_cap") or {}).get("usd"), "ch24": g.get("market_cap_change_percentage_24h_usd"),
            "dom": g.get("market_cap_percentage") or {}}
    return out, glob


def coins_paprika() -> tuple[list, dict]:
    rows = _get("https://api.coinpaprika.com/v1/tickers", {"quotes": "USD"})
    rows = sorted((r for r in rows if r.get("rank")), key=lambda r: r["rank"])[:200]
    out = []
    for r in rows:
        q = (r.get("quotes") or {}).get("USD") or {}
        out.append({"sym": (r.get("symbol") or "").lower(), "name": r.get("name") or "", "cap": q.get("market_cap") or 0, "price": q.get("price"),
                    "c1": q.get("percent_change_24h"), "c7": q.get("percent_change_7d"), "c30": q.get("percent_change_30d")})
    g = _get("https://api.coinpaprika.com/v1/global")
    glob = {"total": g.get("market_cap_usd"), "ch24": g.get("market_cap_change_24h"), "dom": {"btc": g.get("bitcoin_dominance_percentage")}}
    return out, glob


def is_stable(c) -> bool:
    if c["sym"] in STABLE:
        return True
    p = c.get("price") or 0
    return 0.97 <= p <= 1.03 and abs(c.get("c30") or 0) < 2 and "usd" in (c["sym"] + c["name"].lower())


STABLE_30 = 1.0     # tỷ lệ nguồn cung stablecoin 30 ngày trước so với nay (đặt trong run() từ số DefiLlama)


def summarize(coins: list, glob: dict, src: str) -> dict:
    stables = [c for c in coins if is_stable(c)]
    rest = [c for c in coins if not is_stable(c) and c["sym"] not in WRAPPED and c["cap"]]
    top = rest[:100]
    btc = next((c for c in top if c["sym"] == "btc"), None)
    alts = [c for c in top if c["sym"] != "btc"]

    def back(c, k):     # vốn hoá k ngày trước, suy từ % thay đổi giá
        ch = c.get(k)
        return c["cap"] / (1 + ch / 100) if ch is not None and ch > -99 else None
    total = glob.get("total") or sum(c["cap"] for c in coins)
    # 7/30 ngày: tính trên tập top 100 + stablecoin (phủ ~95% vốn hoá), áp tỷ lệ cho tổng
    covered = top + stables
    def ch_total(k):
        now = sum(c["cap"] for c in covered if back(c, k) is not None)
        then = sum(back(c, k) or 0 for c in covered)
        return (now / then - 1) * 100 if then else None
    def ch_alts(k):
        now = sum(c["cap"] for c in alts if back(c, k) is not None)
        then = sum(back(c, k) or 0 for c in alts)
        return (now / then - 1) * 100 if then else None
    scap = sum(c["cap"] for c in stables)
    alt_cap = total - (btc["cap"] if btc else 0) if total else None
    then30 = sum(back(c, "c30") or c["cap"] for c in covered)
    btc_dom = (glob.get("dom") or {}).get("btc") or ((btc["cap"] / total * 100) if btc and total else None)
    btc_dom30 = (back(btc, "c30") / then30 * 100) if btc and back(btc, "c30") and then30 else None
    st_dom = scap / total * 100 if total else None
    st_dom30 = scap * STABLE_30 / then30 * 100 if then30 else None   # nguồn cung stablecoin 30 ngày trước, theo DefiLlama
    br = {}
    for k in ("c1", "c7", "c30"):
        v = [c[k] for c in top if c.get(k) is not None]
        br[k] = {"n": len(v), "down": sum(x < 0 for x in v), "med": round(stx.median(v), 1) if v else None}
    secs = []
    for name in SECTOR:
        m = [c for c in top if SYM2SEC.get(c["sym"]) == name]
        v7 = [c["c7"] for c in m if c.get("c7") is not None]
        v30 = [c["c30"] for c in m if c.get("c30") is not None]
        if len(v30) >= 2:
            secs.append({"name": name, "n": len(v30), "c7": round(stx.median(v7), 1) if v7 else None, "c30": round(stx.median(v30), 1)})
    secs.sort(key=lambda x: -x["c30"])
    ranked = sorted((c for c in top if c.get("c30") is not None), key=lambda c: -c["c30"])
    pick = lambda arr: [{"sym": c["sym"].upper(), "c30": round(c["c30"], 1)} for c in arr]  # noqa: E731
    d7 = br["c7"]["down"] / max(1, br["c7"]["n"])
    if d7 >= 0.8:
        head = ("bad", "Cả thị trường đang giảm, không riêng BTC.")
    elif d7 <= 0.2:
        head = ("good", "Cả thị trường đang tăng.")
    elif btc and btc.get("c7") is not None and br["c7"]["med"] is not None and br["c7"]["med"] - btc["c7"] >= 5:
        head = ("good", "Altcoin đang mạnh hơn BTC.")
    elif btc and btc.get("c7") is not None and br["c7"]["med"] is not None and btc["c7"] - br["c7"]["med"] >= 5:
        head = ("warn", "Tiền dồn về BTC, altcoin yếu hơn.")
    else:
        head = ("neutral", "Thị trường phân hoá: có nhóm tăng, có nhóm giảm.")
    extra = []
    extra.append(f"{br['c7']['down']}/{br['c7']['n']} đồng lớn nhất {'giảm' if d7 >= 0.5 else 'tăng'} trong 7 ngày"
                 if d7 >= 0.5 else f"{br['c7']['n'] - br['c7']['down']}/{br['c7']['n']} đồng lớn nhất tăng trong 7 ngày")
    tc30 = ch_total("c30") if glob.get("total") else None
    if tc30 is not None:
        extra.append(f"tổng vốn hoá {'mất' if tc30 < 0 else 'thêm'} {abs(tc30):.0f}% trong một tháng".replace(".", ","))
    if st_dom is not None and st_dom30 is not None and st_dom - st_dom30 >= 1:
        extra.append("tỷ trọng stablecoin tăng: tiền đang đứng ngoài")
    elif st_dom is not None and st_dom30 is not None and st_dom30 - st_dom >= 1:
        extra.append("tỷ trọng stablecoin giảm: tiền đang quay lại thị trường")
    r = lambda x, d=1: None if x is None else round(x, d)  # noqa: E731
    return {"src": src, "tone": head[0], "head": head[1], "detail": "; ".join(extra) + ".",
            "total": total, "ch24": r(glob.get("ch24")), "ch7": r(ch_total("c7")), "ch30": r(tc30),
            "alt_cap": alt_cap, "alt_ch7": r(ch_alts("c7")), "alt_ch30": r(ch_alts("c30")),
            "btc_dom": r(btc_dom), "btc_dom30": r(btc_dom30), "st_dom": r(st_dom), "st_dom30": r(st_dom30), "st_cap": scap,
            "btc_c7": r(btc.get("c7")) if btc else None, "btc_c30": r(btc.get("c30")) if btc else None,
            "breadth": br, "sectors": secs, "best": pick(ranked[:3]), "worst": pick(ranked[::-1][:3]), "n": len(top)}


def fallback_okx(latest: dict) -> dict:
    """Không gọi được CoinGecko và CoinPaprika: tính độ rộng từ 39 altcoin OKX do compass.py lưu."""
    c = json.load(open(os.path.join(DATA, "compass_cache.json"), encoding="utf-8")).get("alts") or {}
    coins = []
    for s, d in c.items():
        ks = sorted(d, key=int)
        last = int(ks[-1])
        at = lambda k: d.get(str(last - k * 86400))  # noqa: E731
        p = d[ks[-1]]
        coins.append({"sym": s.lower(), "name": s, "cap": 1, "price": p,
                      **{f"c{k}": ((p / at(k) - 1) * 100 if at(k) else None) for k in (1, 7, 30)}})
    out = summarize([{"sym": "btc", "name": "Bitcoin", "cap": 0, "price": None, "c1": None, "c7": None, "c30": None}] + coins, {}, "okx-39")
    for k in ("total", "alt_cap", "btc_dom", "btc_dom30", "st_dom", "st_dom30", "ch7", "ch30", "alt_ch7", "alt_ch30"):
        out[k] = None
    return out


# ---------------- Thị trường thế giới ----------------
WORLD = [  # khoá, tên, mã Yahoo, mã FRED dự phòng, kiểu thay đổi (pct hoặc điểm %)
    ("spx", "S&P 500 (Mỹ)", "^GSPC", "SP500", "pct"),
    ("ndx", "Nasdaq 100 (công nghệ Mỹ)", "^NDX", "NASDAQ100", "pct"),
    ("nikkei", "Nikkei 225 (Nhật)", "^N225", "NIKKEI225", "pct"),
    ("gold", "Vàng", "GC=F", None, "pct"),
    ("oil", "Dầu WTI", "CL=F", "DCOILWTICO", "pct"),
    ("usd", "Chỉ số USD", "DX-Y.NYB", "DTWEXBGS", "pct"),
    ("us10", "Lợi suất Mỹ 10 năm", "^TNX", "DGS10", "pt"),
]
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _yahoo(sym: str) -> list:
    last = None
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}",
                             params={"range": "3mo", "interval": "1d"}, headers=BROWSER, timeout=20)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            cl = res["indicators"]["quote"][0]["close"]
            out = [(int(t), float(c)) for t, c in zip(res["timestamp"], cl) if c is not None]
            if len(out) >= 25:
                return out
            raise RuntimeError("thiếu dữ liệu")
        except Exception as ex:  # noqa: BLE001
            last = ex
    raise last


def _fred(sid: str) -> list:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("chưa có FRED_API_KEY")
    start = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d")
    j = _get("https://api.stlouisfed.org/fred/series/observations", {"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start})
    out = [(int(datetime.strptime(o["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()), float(o["value"]))
           for o in j.get("observations", []) if o.get("value") not in (".", "", None)]
    if len(out) < 25:
        raise RuntimeError(f"{sid}: thiếu dữ liệu")
    return out


def _chg(ser: list, days: int, kind: str):
    t1, v1 = ser[-1]
    prev = [v for t, v in ser if t <= t1 - days * 86400 + 3600]
    if not prev:
        return None
    v0 = prev[-1]
    return round(v1 - v0, 2) if kind == "pt" else round((v1 / v0 - 1) * 100, 1)


def world(mkt: dict) -> dict:
    items, errs = [], []
    for key, name, ysym, fsid, kind in WORLD:
        ser, src = None, None
        for sname, fn in (("Yahoo", lambda: _yahoo(ysym)), ("FRED", (lambda: _fred(fsid)) if fsid else None)):
            if fn is None:
                continue
            try:
                ser, src = fn(), sname
                break
            except Exception as ex:  # noqa: BLE001
                errs.append(f"{name} ({sname}): {str(ex)[:80]}")
        if not ser:
            continue
        if kind == "pt" and ser[-1][1] > 20:      # Yahoo ^TNX cũ trả x10
            ser = [(t, v / 10) for t, v in ser]
        items.append({"key": key, "name": name, "val": round(ser[-1][1], 2), "asof": datetime.fromtimestamp(ser[-1][0], timezone.utc).strftime("%Y-%m-%d"),
                      "kind": kind, "c1": _chg(ser, 1, kind), "c7": _chg(ser, 7, kind), "c30": _chg(ser, 30, kind), "src": src})
    eq = next((i for i in items if i["key"] == "spx"), None) or next((i for i in items if i["key"] == "ndx"), None)
    cr7 = mkt.get("ch7") if mkt.get("ch7") is not None else mkt.get("btc_c7")
    head, tone = None, "neutral"
    if eq and eq.get("c7") is not None and cr7 is not None:
        e7 = eq["c7"]
        if e7 <= -2 and cr7 <= -5:
            head, tone = "Rủi ro toàn cầu (risk-off): cả chứng khoán và crypto cùng giảm. Nguyên nhân chủ yếu từ vĩ mô; crypto thường chỉ hồi khi chứng khoán hồi.", "bad"
        elif e7 > -1 and cr7 <= -5:
            head, tone = "Chỉ crypto giảm, chứng khoán vẫn ổn: vấn đề riêng của crypto (sự cố, ETF rút, xả đòn bẩy). Khi tin xấu qua đi, giá thường hồi nhanh hơn.", "warn"
        elif e7 >= 2 and cr7 >= 5:
            head, tone = "Ưa rủi ro toàn cầu (risk-on): cả chứng khoán và crypto cùng tăng.", "good"
        elif e7 <= -2 and cr7 > -1:
            head, tone = "Crypto đứng vững dù chứng khoán giảm.", "good"
        elif e7 >= 1 and cr7 <= -2:
            head, tone = "Chứng khoán tăng nhưng crypto không theo: crypto đang yếu riêng.", "warn"
        else:
            head = "Chứng khoán và crypto chưa có diễn biến rõ cùng chiều."
        paren = f"7 ngày: {eq['name'].split(' (')[0]} {e7:+.1f}%; toàn thị trường crypto {cr7:+.1f}%".replace(".", ",").replace("-", "−")
        head = f"{head} ({paren})"
    return {"items": items, "head": head, "tone": tone, "err": "; ".join(errs[:4]) or None}


# ---------------- Lịch crypto ----------------
# ---------- Dòng tiền ngoài crypto: 11 ngành chứng khoán Mỹ, quỹ tiền tệ Mỹ (ICI), vị thế quỹ trên Bitcoin CME (CFTC COT) ----------
SECTORS = [("XLK", "Công nghệ", "tc"), ("XLC", "Viễn thông, truyền thông", "tc"), ("XLY", "Tiêu dùng không thiết yếu", "tc"), ("XLI", "Công nghiệp", "tc"),
           ("XLF", "Tài chính, ngân hàng", ""), ("XLB", "Nguyên vật liệu", ""), ("XLE", "Năng lượng", ""),
           ("XLP", "Hàng thiết yếu", "pt"), ("XLU", "Tiện ích (điện, nước)", "pt"), ("XLV", "Y tế", "pt"), ("XLRE", "Bất động sản", "pt")]


def sectors() -> dict:
    spy = _yahoo("SPY")
    s30 = _chg(spy, 30, "pct")
    out, errs = [], []
    for sym, name, grp in SECTORS:
        try:
            ser = _yahoo(sym)
            c30 = _chg(ser, 30, "pct")
            out.append({"sym": sym, "name": name, "grp": grp, "c30": c30, "c7": _chg(ser, 7, "pct"), "rel": round(c30 - s30, 1) if c30 is not None and s30 is not None else None})
        except Exception as ex:  # noqa: BLE001
            errs.append(f"{sym}: {str(ex)[:60]}")
    ok = [x for x in out if x["rel"] is not None]
    if len(ok) < 8:
        raise RuntimeError("thiếu ngành: " + "; ".join(errs[:3]))
    avg = lambda g: stx.mean([x["c30"] for x in ok if x["grp"] == g])  # noqa: E731
    gap = round(avg("pt") - avg("tc"), 1)
    ok.sort(key=lambda x: -x["rel"])
    return {"spy30": s30, "items": ok, "gap": gap, "asof": datetime.fromtimestamp(spy[-1][0], timezone.utc).strftime("%Y-%m-%d"), "err": "; ".join(errs) or None}


MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}


def _ici_page(url: str):
    import re
    r = requests.get(url, headers=BROWSER, timeout=25)
    r.raise_for_status()
    txt = re.sub(r"<[^>]+>", " ", r.text)
    txt = re.sub(r"\s+", " ", txt)
    m = re.search(r"Total money market fund assets\W*\d?\s*(increased|decreased) by \$([\d.,]+) (billion|million) to \$([\d.,]+) trillion for the week ended \w+, (\w+) (\d+)", txt)
    if not m:
        raise RuntimeError("không thấy câu tổng tài sản")
    chg = float(m.group(2).replace(",", "")) * (1 if m.group(3) == "billion" else 0.001) * (1 if m.group(1) == "increased" else -1)
    today = date.today()
    mo, dy = MONTHS[m.group(5)], int(m.group(6))
    y = today.year if (mo, dy) <= (today.month, today.day) or mo <= today.month + 1 else today.year - 1
    return {"d": date(y, mo, dy).isoformat(), "total": float(m.group(4)), "chg": round(chg, 2)}


def mmf(old: dict) -> dict:
    hist = {h["d"]: h for h in (old or {}).get("hist", [])}
    cur = _ici_page("https://www.ici.org/research/stats/mmf")
    hist[cur["d"]] = cur
    # Bù các tuần trước từ trang lưu trữ (ra thứ Năm, sau ngày chốt thứ Tư 1 ngày)
    d0 = date.fromisoformat(cur["d"])
    for k in range(1, 9):
        dk = d0 - timedelta(days=7 * k)
        if dk.isoformat() in hist:
            continue
        rel = dk + timedelta(days=1)
        try:
            h = _ici_page(f"https://www.ici.org/research/stats/mmf/mm_{rel:%m_%d_%y}")
            hist[h["d"]] = h
        except Exception:  # noqa: BLE001
            pass
    hs = sorted(hist.values(), key=lambda h: h["d"])[-60:]
    last4 = [h for h in hs if h["d"] > (d0 - timedelta(days=28)).isoformat()]
    c4 = sum(h["chg"] for h in last4) if len(last4) >= 4 else None
    return {"d": cur["d"], "total": cur["total"], "w1": cur["chg"], "c4": round(c4, 1) if c4 is not None else None,
            "c4p": round(c4 / (cur["total"] * 1000 - c4) * 100, 2) if c4 is not None else None, "hist": hs}


def cot() -> dict:
    url = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
    rows = None
    for where in ("cftc_contract_market_code='133741'", "upper(market_and_exchange_names) like 'BITCOIN - CHICAGO MERCANTILE%'"):
        try:
            rows = _get(url, {"$where": where, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 12})
            if rows:
                break
        except Exception:  # noqa: BLE001
            rows = None
    if not rows:
        raise RuntimeError("CFTC không trả dữ liệu Bitcoin CME")
    f = lambda r, k: float(r.get(k) or 0)  # noqa: E731
    pts = [{"d": r["report_date_as_yyyy_mm_dd"][:10], "am": f(r, "asset_mgr_positions_long") - f(r, "asset_mgr_positions_short"),
            "lev": f(r, "lev_money_positions_long") - f(r, "lev_money_positions_short"), "oi": f(r, "open_interest_all")} for r in rows]
    a, b = pts[0], (pts[4] if len(pts) > 4 else pts[-1])
    ch = round((a["am"] - b["am"]) / abs(b["am"]) * 100, 1) if b["am"] else None
    return {"d": a["d"], "am": a["am"], "am4": b["am"], "am_ch": ch, "lev": a["lev"], "oi": a["oi"], "pts": pts[::-1]}


def last_friday(y: int, m: int) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def deribit(cur: str) -> dict:
    J = _get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency", {"currency": cur, "kind": "option"}).get("result") or []
    by = {}
    for r in J:
        try:
            _, exp, strike, cp = r["instrument_name"].split("-")
            by.setdefault(exp, []).append((float(strike), cp, float(r.get("open_interest") or 0), float(r.get("underlying_price") or r.get("estimated_delivery_price") or 0)))
        except (KeyError, ValueError):
            continue
    out = {}
    for exp, L in by.items():
        try:
            d = datetime.strptime(exp, "%d%b%y").date()
        except ValueError:
            continue
        px = next((u for *_, u in L if u), 0)
        calls = sum(oi for _, cp, oi, _ in L if cp == "C")
        puts = sum(oi for _, cp, oi, _ in L if cp == "P")
        strikes = sorted({k for k, *_ in L})
        def pain(s):
            return sum(oi * max(0.0, s - k) for k, cp, oi, _ in L if cp == "C") + sum(oi * max(0.0, k - s) for k, cp, oi, _ in L if cp == "P")
        mp = min(strikes, key=pain) if strikes else None
        out[d.isoformat()] = {"oi": round(calls + puts, 1), "usd": round((calls + puts) * px), "pc": round(puts / calls, 2) if calls else None,
                              "max_pain": mp, "px": round(px, 2)}
    return out


def calendar(old: dict) -> dict:
    today = datetime.now(timezone.utc).date()
    big = []
    seen = set()
    for i in range(0, 5):
        y, m = today.year + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1
        d = last_friday(y, m)
        if d >= today and d not in seen:
            seen.add(d)
            big.append({"date": d.isoformat(), "kind": "quarter" if m in (3, 6, 9, 12) else "month"})
    big = big[:3]
    opt, err = {}, None
    for cur in ("BTC", "ETH"):
        try:
            opt[cur] = deribit(cur)
        except Exception as ex:  # noqa: BLE001
            err = f"Deribit {cur}: {str(ex)[:100]}"
            opt[cur] = (old.get("opt_raw") or {}).get(cur) or {}
    for e in big:
        e["btc"] = opt.get("BTC", {}).get(e["date"])
        e["eth"] = opt.get("ETH", {}).get(e["date"])
        e["cme"] = True       # hợp đồng tương lai BTC của CME hết hạn thứ Sáu cuối tháng (trừ khi trùng ngày nghỉ)
    keep = {cur: {d: v for d, v in opt.get(cur, {}).items() if d in {e["date"] for e in big}} for cur in opt}
    manual = []
    try:
        M = json.load(open(os.path.join(DATA, "events_manual.json"), encoding="utf-8")).get("events") or []
        manual = [e for e in M if e.get("date", "") >= today.isoformat()]
    except (OSError, ValueError):
        pass
    return {"expiries": big, "manual": manual, "opt_raw": keep, "opt_err": err}


def run(latest: dict) -> dict:
    global STABLE_30
    old = _load()
    now = time.time()
    try:
        stb = (latest.get("market") or {}).get("stable") or {}
        STABLE_30 = (stb["total"] - stb["d30"]) / stb["total"]
    except (KeyError, TypeError, ZeroDivisionError):
        STABLE_30 = 1.0
    data = dict(old)
    if now - old.get("fetched", 0) >= REFRESH or not old.get("mkt"):
        errs = []
        mkt = None
        for name, fn in (("coingecko", coins_coingecko), ("coinpaprika", coins_paprika)):
            try:
                coins, glob = fn()
                if len(coins) >= 80:
                    mkt = summarize(coins, glob, name)
                    break
                errs.append(f"{name}: chỉ {len(coins)} đồng")
            except Exception as ex:  # noqa: BLE001
                errs.append(f"{name}: {str(ex)[:120]}")
        if mkt is None:
            try:
                mkt = fallback_okx(latest)
            except Exception as ex:  # noqa: BLE001
                errs.append(f"okx: {str(ex)[:120]}")
        if mkt:
            data["mkt"] = mkt
        data["mkt_err"] = "; ".join(errs) or None
        try:
            data["world"] = world(data.get("mkt") or {})
        except Exception as ex:  # noqa: BLE001
            data["world_err"] = str(ex)[:200]
        for key, fn2 in (("sectors", sectors), ("cot", cot)):  # ICI (quỹ tiền tệ) chặn máy chủ GitHub (403) nên tắt
            try:
                data[key] = fn2()
                data.pop(key + "_err", None)
            except Exception as ex:  # noqa: BLE001
                data[key + "_err"] = str(ex)[:200]
        try:
            data["cal"] = calendar(old.get("cal") or {})
        except Exception as ex:  # noqa: BLE001
            data["cal_err"] = str(ex)[:200]
        data["fetched"] = now
    data["news"] = [{k: p.get(k) for k in ("t", "n_src", "outlet", "text", "vi", "url", "cat")} for p in (latest.get("news_big") or [])]
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    m = data.get("mkt") or {}
    return {"head": m.get("head"), "tone": m.get("tone"), "src": m.get("src"), "err": data.get("mkt_err"), "cal_err": (data.get("cal") or {}).get("opt_err"),
            "expiries": [{k: e.get(k) for k in ("date", "kind")} | {"btc_usd": (e.get("btc") or {}).get("usd")} for e in (data.get("cal") or {}).get("expiries", [])]}


if __name__ == "__main__":
    print(json.dumps(run(json.load(open(os.path.join(DATA, "latest.json"), encoding="utf-8"))), ensure_ascii=False))
