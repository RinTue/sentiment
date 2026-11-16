"""La bàn thị trường: thị trường đang ở trạng thái nào (không phải dự báo).

Ý tưởng theo Market Compass của Glassnode, dựng lại bằng nguồn miễn phí:
- 4 nhóm chính (cộng thành điểm tổng): Vĩ mô, Dòng tiền, Hành vi nhà đầu tư, Nền tảng on-chain.
- 3 mục đọc riêng (không cộng vào điểm tổng): Vị trí chu kỳ, Phái sinh, Luân chuyển BTC/altcoin.
Mỗi chỉ báo được xếp hạng (phân vị 0–100) so với chính lịch sử của nó trong 3 năm trước đó (chu kỳ dùng toàn bộ lịch sử
từ 2018), chỉ dùng dữ liệu đến ngày đó. Điểm nhóm = trung bình các chỉ báo; điểm cao = thuận lợi / nhiều rủi ro hơn.
Tính lại mỗi ngày một lần trong history_test.run(), dùng chung dữ liệu đã tải ở đó.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DAY = 86400
COMPASS_V = 1
UA = "sentiment-dashboard (github.com/RinTue/sentiment)"
WIN = 1095          # 3 năm
ALTS = ["ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "LINK", "AVAX"]

LENSES = [
    # key, tên, chính?, 5 vùng từ thấp đến cao, giải thích ngắn
    ("macro", "Vĩ mô", True, ["Thắt chặt", "Hạn chế", "Trung tính", "Hỗ trợ", "Nới lỏng"],
     "Đồng đô la, lợi suất trái phiếu, thanh khoản của Fed, chênh lệch lãi suất trái phiếu rủi ro."),
    ("flows", "Dòng tiền", True, ["Cạn kiệt", "Yếu", "Cân bằng", "Dồi dào", "Tràn ngập"],
     "Nguồn stablecoin, dòng tiền ETF Bitcoin, Coinbase Premium (người Mỹ mua hay bán)."),
    ("behav", "Hành vi nhà đầu tư", True, ["Rút lui", "Dè dặt", "Trung tính", "Hăng hái", "Hưng phấn"],
     "Fear & Greed, người mới tìm cách mua Bitcoin trên Google, độ quan tâm tới memecoin."),
    ("fund", "Nền tảng on-chain", True, ["Co hẹp", "Yếu", "Ổn định", "Mở rộng", "Sôi động"],
     "Số địa chỉ hoạt động, số giao dịch, phí mạng, sức mạnh đào (hashrate) của Bitcoin."),
    ("cycle", "Vị trí chu kỳ", False, ["Đầu hàng", "Tích luỹ", "Giữa chu kỳ", "Phân phối", "Hưng phấn"],
     "Giá so với trung bình 200 ngày và 200 tuần, mức giảm từ đỉnh, lãi 1 năm (so với toàn bộ lịch sử từ 2018)."),
    ("deriv", "Phái sinh", False, ["Xả đòn bẩy", "Nhẹ", "Cân bằng", "Nóng", "Quá nóng"],
     "Funding, open interest so với quá khứ, tốc độ tăng open interest."),
    ("rot", "Luân chuyển", False, ["Mùa BTC", "Nghiêng BTC", "Cân bằng", "Nghiêng altcoin", "Mùa altcoin"],
     "Altcoin lớn chạy hơn hay thua BTC trong 30 ngày, ETH/BTC 90 ngày."),
]
HEAD_BANDS = ["Né rủi ro", "Phòng thủ", "Trung tính", "Tích cực", "Ưa rủi ro"]


# ---------------------------------------------------------------- tải dữ liệu
def _get(url: str, params: dict | None = None, timeout: int = 30):
    r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def day_of(ts: float) -> int:
    return int(ts // DAY * DAY)


def fred(sid: str, start: str = "2018-06-01") -> dict:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("chưa có FRED_API_KEY")
    j = _get("https://api.stlouisfed.org/fred/series/observations",
             {"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start})
    out = {}
    for o in j.get("observations", []):
        if o.get("value") not in (".", "", None):
            out[int(datetime.strptime(o["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())] = float(o["value"])
    if len(out) < 50:
        raise RuntimeError(f"{sid}: thiếu dữ liệu")
    return out


def chain(name: str) -> dict:
    """Biểu đồ công khai của blockchain.com (không cần mã)."""
    j = _get(f"https://api.blockchain.info/charts/{name}", {"timespan": "6years", "format": "json", "sampled": "false"}, timeout=40)
    out = {day_of(v["x"]): float(v["y"]) for v in j.get("values", []) if v.get("y") is not None}
    if len(out) < 100:
        raise RuntimeError(f"{name}: thiếu dữ liệu")
    return out


def okx_daily(sym: str, since: float) -> dict:
    out, after = {}, None
    for _ in range(30):
        p = {"instId": f"{sym}-USDT", "bar": "1Dutc", "limit": 100}
        if after:
            p["after"] = after
        rows = _get("https://www.okx.com/api/v5/market/history-candles", p).get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0]) // 1000] = float(r[4])
        after = rows[-1][0]
        if int(after) // 1000 < since:
            break
        time.sleep(0.15)
    return out


def etf_daily() -> dict:
    try:
        J = json.load(open(os.path.join(DATA, "etf.json"), encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {int(datetime.strptime(r[0], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()): r[1] for r in J.get("days") or []}


# ---------------------------------------------------------------- xử lý chuỗi
def ffill(s: dict, until: int | None = None, max_gap: int = 10) -> dict:
    """Lấp ngày trống bằng giá trị gần nhất trước đó (số liệu theo tuần, ngày nghỉ), tối đa max_gap ngày."""
    if not s:
        return {}
    days = sorted(s)
    out, j = {}, 0
    end = until or days[-1]
    d = days[0]
    while d <= end:
        while j + 1 < len(days) and days[j + 1] <= d:
            j += 1
        if d - days[j] <= max_gap * DAY:
            out[d] = s[days[j]]
        d += DAY
    return out


def sma(s: dict, n: int) -> dict:
    days = sorted(s)
    out, acc = {}, 0.0
    for i, d in enumerate(days):
        acc += s[d]
        if i >= n:
            acc -= s[days[i - n]]
        if i >= n - 1:
            out[d] = acc / n
    return out


def chg(s: dict, n: int, pct: bool = True) -> dict:
    return {d: ((v / s[d - n * DAY] - 1) * 100 if pct else v - s[d - n * DAY]) for d, v in s.items()
            if s.get(d - n * DAY) not in (None, 0)}


def rank(s: dict, window: int | None = WIN, min_n: int = 180, invert: bool = False) -> dict:
    """Phân vị của mỗi ngày so với các ngày trước đó (window ngày, None = toàn bộ quá khứ)."""
    import bisect
    days = sorted(s)
    out, buf = {}, []
    lo = 0
    for i, d in enumerate(days):
        bisect.insort(buf, s[d])
        if window:
            while days[lo] < d - window * DAY:
                buf.pop(bisect.bisect_left(buf, s[days[lo]]))
                lo += 1
        if len(buf) >= min_n:
            p = 100 * bisect.bisect_right(buf, s[d]) / len(buf)
            out[d] = 100 - p if invert else p
    return out


def band(v: float | None, names: list) -> str | None:
    return None if v is None else names[min(4, int(v // 20))]


# ---------------------------------------------------------------- tính la bàn
def run(px: dict, fng: dict, stab: dict, prem_daily: dict, cz: dict, wiki: dict, meme_ratio: dict, trends: dict, f7: dict, f30: dict) -> dict:
    t0, errs = time.time(), {}

    def safe(name, fn):
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001
            errs[name] = str(ex)[:140]
            return {}

    since = datetime(2018, 6, 1, tzinfo=timezone.utc).timestamp()
    last_px = max(px)
    IND: dict = {}      # key -> (lens, tên, phân vị theo ngày, giá trị gốc theo ngày, cách hiển thị)

    def add(lens, key, name, score, raw, fmt):
        if score:
            IND[key] = (lens, name, score, raw, fmt)

    # Vĩ mô
    usd = ffill(safe("fred_usd", lambda: fred("DTWEXBGS")), last_px)
    y2 = ffill(safe("fred_y2", lambda: fred("DGS2")), last_px)
    real = ffill(safe("fred_real", lambda: fred("DFII10")), last_px)
    hy = ffill(safe("fred_hy", lambda: fred("BAMLH0A0HYM2")), last_px)
    walcl = ffill(safe("fred_walcl", lambda: fred("WALCL")), last_px)
    tga = ffill(safe("fred_tga", lambda: fred("WTREGEN")), last_px)
    rrp = ffill(safe("fred_rrp", lambda: fred("RRPONTSYD")), last_px)
    if usd:
        c = chg(usd, 63)
        add("macro", "usd", "Đồng đô la (thay đổi 3 tháng)", rank(c, invert=True), c, "pct")
    if y2:
        c = chg(y2, 63, pct=False)
        add("macro", "y2", "Lợi suất 2 năm, theo kỳ vọng lãi suất Fed (thay đổi 3 tháng)", rank(c, invert=True), {d: v * 100 for d, v in c.items()}, "bp")
    if real:
        add("macro", "real", "Lợi suất thực 10 năm (mức)", rank(real, invert=True), real, "pct_lvl")
    if walcl and tga and rrp:
        nl = {d: walcl[d] / 1000 - tga[d] / 1000 - rrp[d] for d in walcl if d in tga and d in rrp}
        c = chg(nl, 91)
        add("macro", "netliq", "Thanh khoản ròng của Fed (thay đổi 13 tuần)", rank(c), c, "pct")
    if hy:
        add("macro", "hy", "Chênh lệch lãi suất trái phiếu rủi ro (mức)", rank(hy, invert=True), hy, "pct_lvl")

    # Dòng tiền
    if stab:
        st = ffill(stab, last_px)
        c = chg(st, 30)
        add("flows", "stable", "Nguồn stablecoin (thay đổi 30 ngày)", rank(c), c, "pct")
    etf = etf_daily()
    if etf:
        ds = sorted(etf)
        s20 = {ds[i]: sum(etf[x] for x in ds[i - 19:i + 1]) for i in range(19, len(ds))}
        s20 = ffill(s20, last_px, 4)
        add("flows", "etf", "ETF Bitcoin ở Mỹ (tổng 20 phiên)", rank(s20, min_n=120), s20, "musd")
    if prem_daily:
        p7 = sma(prem_daily, 7)
        add("flows", "prem", "Coinbase Premium (trung bình 7 ngày)", rank(p7), p7, "pct3")

    # Hành vi nhà đầu tư (tâm lý đám đông)
    if fng:
        f7a = sma(ffill(fng, last_px), 7)
        add("behav", "fng", "Fear & Greed (trung bình 7 ngày)", rank(f7a), f7a, "int")
    if trends:
        add("behav", "trends", "Google: tìm \"buy bitcoin\" toàn cầu", rank(trends), trends, "int")
    if meme_ratio:
        m7 = sma(meme_ratio, 7)
        add("behav", "meme", "Quan tâm memecoin so với Bitcoin (Wikipedia)", rank(m7), m7, "ratio")

    # Nền tảng on-chain
    for key, chart, name, mode in (("addr", "n-unique-addresses", "Địa chỉ hoạt động (trung bình 30 ngày)", "lvl"),
                                   ("tx", "n-transactions", "Số giao dịch (trung bình 30 ngày)", "lvl"),
                                   ("fees", "transaction-fees-usd", "Phí mạng, $ (trung bình 30 ngày)", "lvl"),
                                   ("hash", "hash-rate", "Hashrate (thay đổi 30 ngày)", "chg")):
        s = safe("chain_" + key, lambda c=chart: chain(c))
        if not s:
            continue
        s = ffill(s, last_px, 5)
        if mode == "lvl":
            m = sma(s, 30)
            add("fund", key, name, rank(m), m, "usd" if key == "fees" else "int")
        else:
            m = chg(sma(s, 7), 30)
            add("fund", key, name, rank(m), m, "pct")
        time.sleep(1)

    # Vị trí chu kỳ (toàn bộ lịch sử giá từ 2018)
    p = dict(px)
    m200 = sma(p, 200)
    mayer = {d: p[d] / m200[d] for d in m200}
    add("cycle", "mayer", "Giá / trung bình 200 ngày", rank(mayer, None, 365), mayer, "x")
    w200 = sma(p, 1400)
    r200w = {d: p[d] / w200[d] for d in w200}
    add("cycle", "w200", "Giá / trung bình 200 tuần", rank(r200w, None, 120), r200w, "x")
    ath, mx = {}, 0.0
    for d in sorted(p):
        mx = max(mx, p[d])
        ath[d] = (p[d] / mx - 1) * 100
    add("cycle", "dd", "Cách đỉnh cao nhất", rank({d: v for d, v in ath.items() if d >= since + 365 * DAY}, None, 365), ath, "pct")
    r365 = chg(p, 365)
    add("cycle", "r365", "Lãi/lỗ 1 năm", rank(r365, None, 365), r365, "pct")

    # Phái sinh
    if cz.get("funding"):
        fd = sma(cz["funding"], 7)
        add("deriv", "funding", "Funding (trung bình 7 ngày)", rank(fd), {d: v * 100 for d, v in fd.items()}, "pct4")
    oi_btc = {d: v / px[d] for d, v in (cz.get("oi_usd") or {}).items() if px.get(d)}
    if oi_btc:
        add("deriv", "oi", "Open interest tính theo BTC (mức)", rank(oi_btc, min_n=120), oi_btc, "btc")
        c = chg(oi_btc, 30)
        add("deriv", "oi30", "Open interest (thay đổi 30 ngày)", rank(c, min_n=120), c, "pct")

    # Luân chuyển
    alts = {}
    for a in ALTS:
        s = safe("okx_" + a, lambda a=a: okx_daily(a, since))
        if s:
            alts[a] = s
    if alts:
        b30 = chg(p, 30)
        rel = {}
        for d in b30:
            xs = [chg_one(alts[a], d, 30) - b30[d] for a in alts if chg_one(alts[a], d, 30) is not None]
            if len(xs) >= max(3, len(alts) // 2):
                rel[d] = statistics.median(xs)
        add("rot", "alt30", f"{len(alts)} altcoin lớn so với BTC (trung vị, 30 ngày)", rank(rel), rel, "pp")
        if "ETH" in alts:
            eb = {d: alts["ETH"][d] / p[d] for d in alts["ETH"] if p.get(d)}
            c = chg(eb, 90)
            add("rot", "ethbtc", "ETH/BTC (thay đổi 90 ngày)", rank(c), c, "pct")

    # Gộp theo nhóm
    by_lens: dict = {}
    for k, (lens, *_r) in IND.items():
        by_lens.setdefault(lens, []).append(k)
    L: dict = {}
    for lens, keys in by_lens.items():
        alld = sorted(set().union(*[set(IND[k][2]) for k in keys]))
        need = max(1, math.ceil(len(keys) / 2))
        sc = {}
        for d in alld:
            v = [IND[k][2][d] for k in keys if d in IND[k][2]]
            if len(v) >= need:
                sc[d] = sum(v) / len(v)
        L[lens] = sc
    mains = [x[0] for x in LENSES if x[2] and x[0] in L]
    head = {}
    if mains:
        for d in sorted(set().union(*[set(L[k]) for k in mains])):
            v = [L[k][d] for k in mains if d in L[k]]
            if len(v) >= max(2, len(mains) - 1):
                head[d] = sum(v) / len(v)
    if not head:
        return {"v": COMPASS_V, "error": "không đủ dữ liệu", "errors": errs}
    last = max(head)

    def d7(s):
        a = s.get(last)
        b = s.get(last - 7 * DAY)
        return round(a - b) if a is not None and b is not None else None

    def fmt(v, kind):
        if v is None:
            return "–"
        if kind == "pct":
            return f"{v:+.1f}%"
        if kind == "pct3":
            return f"{v:+.3f}%"
        if kind == "pct4":
            return f"{v:+.4f}%"
        if kind == "pct_lvl":
            return f"{v:.2f}%"
        if kind == "bp":
            return f"{v:+.0f} điểm cơ bản"
        if kind == "musd":
            return f"{v / 1000:+.2f} tỷ $" if abs(v) >= 1000 else f"{v:+.0f} triệu $"
        if kind == "usd":
            return f"{v / 1e6:.2f} triệu $" if v >= 1e6 else f"{v:,.0f} $"
        if kind == "x":
            return f"{v:.2f} lần"
        if kind == "pp":
            return f"{v:+.1f} điểm %"
        if kind == "btc":
            return f"{v:,.0f} BTC"
        if kind == "ratio":
            return f"{v:.3f}"
        return f"{v:,.0f}"

    def vfmt(v, kind):
        t = fmt(v, kind)
        return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".").replace("-", "−")

    lenses = []
    for key, name, main, bands, desc in LENSES:
        sc = L.get(key)
        if not sc:
            lenses.append({"key": key, "name": name, "main": main, "bands": bands, "desc": desc, "score": None, "missing": True})
            continue
        lk = max(sc)
        items = []
        for k in by_lens.get(key, []):
            _, nm, s, raw, kind = IND[k]
            dd = max(s)
            items.append({"key": k, "name": nm, "p": round(s[dd]), "raw": vfmt(raw.get(dd), kind),
                          "asof": datetime.fromtimestamp(dd, timezone.utc).strftime("%Y-%m-%d"), "d7": round(s[dd] - s[dd - 7 * DAY]) if dd - 7 * DAY in s else None})
        v = sc[lk]
        lenses.append({"key": key, "name": name, "main": main, "bands": bands, "desc": desc, "score": round(v), "band": band(v, bands),
                       "d7": round(v - sc[lk - 7 * DAY]) if lk - 7 * DAY in sc else None,
                       "asof": datetime.fromtimestamp(lk, timezone.utc).strftime("%Y-%m-%d"), "items": items})

    # Độ đồng thuận giữa 4 nhóm chính
    mv = [L[k][last] for k in mains if last in L[k]]
    sd = statistics.pstdev(mv) if len(mv) > 1 else 0
    conv = max(0, min(100, round(100 - 2.2 * sd)))
    above, below = sum(1 for x in mv if x > 55), sum(1 for x in mv if x < 45)
    conv_lbl = "Đồng thuận" if (above == 0 or below == 0) and conv >= 60 else "Lệch nhau" if above and below else "Khá đồng thuận"

    # Kiểm chứng: điểm tổng ở từng vùng, BTC 7 và 30 ngày sau thế nào
    def stats(sel, f):
        xs = [f[d] for d in sel if d in f]
        if not xs:
            return None
        return {"n": len(xs), "up": round(100 * sum(1 for x in xs if x > 0) / len(xs)), "med": round(statistics.median(xs), 1)}
    bt = []
    for i, nm in enumerate(HEAD_BANDS):
        sel = [d for d, v in head.items() if min(4, int(v // 20)) == i]
        bt.append({"name": nm, "lo": i * 20, "hi": i * 20 + 20, "days": len(sel), "d7": stats(sel, f7), "d30": stats(sel, f30)})
    allb = {"d7": stats(list(head), f7), "d30": stats(list(head), f30)}

    series = []
    for d in sorted(head):
        if d >= last - 730 * DAY:
            series.append([d * 1000, round(head[d])] + [round(L[k][d]) if d in L.get(k, {}) else None for k in ("macro", "flows", "behav", "fund")])
    hv = head[last]
    out = {"v": COMPASS_V, "asof": datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%d"),
           "head": {"score": round(hv), "band": band(hv, HEAD_BANDS), "bands": HEAD_BANDS, "d7": d7(head), "n_main": len(mains)},
           "conviction": {"v": conv, "label": conv_lbl}, "lenses": lenses, "bt": bt, "bt_all": allb,
           "bt_from": datetime.fromtimestamp(min(head), timezone.utc).strftime("%Y-%m-%d"),
           "series": series, "errors": errs, "runtime_sec": round(time.time() - t0, 1)}
    return out


def chg_one(s: dict, d: int, n: int):
    a, b = s.get(d), s.get(d - n * DAY)
    return (a / b - 1) * 100 if a and b else None
