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
COMPASS_V = 4
UA = "sentiment-dashboard (github.com/RinTue/sentiment)"
WIN = 1095          # 3 năm
ALTS = ["ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "LINK", "AVAX"]
# Rổ altcoin cho chỉ số mùa altcoin (các đồng lớn có trên OKX, không tính stablecoin)
ALTS_ALL = ALTS + ["TRX", "SUI", "XLM", "TON", "HBAR", "BCH", "LTC", "DOT", "UNI", "NEAR", "APT", "ICP", "ETC", "AAVE", "FIL",
                   "ATOM", "ARB", "OP", "INJ", "PEPE", "SHIB", "WLD", "SEI", "TIA", "ALGO", "ENA", "ONDO", "POL", "CRV", "LDO",
                   "STX", "IMX"]
CACHE = os.path.join(DATA, "compass_cache.json")

LENSES = [
    # key, tên, chính?, 5 vùng từ thấp đến cao, giải thích ngắn
    ("macro", "Vĩ mô", True, ["Thắt chặt", "Hạn chế", "Trung tính", "Hỗ trợ", "Nới lỏng"],
     "Như Glassnode: đồng đô la so với trung bình 200 ngày, đường cong lợi suất, kỳ vọng lãi suất Fed (FRED)."),
    ("flows", "Dòng tiền", True, ["Cạn kiệt", "Yếu", "Cân bằng", "Dồi dào", "Tràn ngập"],
     "Như Glassnode: stablecoin 30 ngày, số BTC nằm trên sàn (Coin Metrics), lực mua spot ròng (Coinalyze), ETF Mỹ."),
    ("behav", "Hành vi nhà đầu tư", True, ["Rút lui", "Dè dặt", "Trung tính", "Hăng hái", "Hưng phấn"],
     "Như Glassnode: tỷ lệ cung của người giữ dài hạn và mức gom/bán 30 ngày (BGeometrics, lượng cung theo tuổi coin), MVRV người giữ ngắn hạn (trễ ~7 ngày). Glassnode tính người giữ dài hạn theo 155 ngày, ở đây theo 6 tháng."),
    ("fund", "Nền tảng on-chain", True, ["Co hẹp", "Yếu", "Ổn định", "Mở rộng", "Sôi động"],
     "Như Glassnode: đà phí mạng, mức tham gia, thanh toán và độ đầy khối (trung bình 30 ngày so với 90 ngày, blockchain.com)."),
    ("cycle", "Vị trí chu kỳ", False, ["Đầu hàng", "Tích luỹ", "Giữa chu kỳ", "Phân phối", "Hưng phấn"],
     "Như Glassnode: tỷ lệ lãi/lỗ đã chốt trên chuỗi (BGeometrics) và giá so với giá vốn thực (MVRV, Coin Metrics, trễ khoảng 1 tháng nhưng giá vốn đổi chậm)."),
    ("deriv", "Phái sinh", False, ["Xả đòn bẩy", "Nhẹ", "Cân bằng", "Nóng", "Quá nóng"],
     "Như Glassnode: funding, open interest so với vốn hoá, độ biến động kỳ vọng DVOL (Deribit). Chưa có chênh lệch hợp đồng quý và độ lệch quyền chọn."),
    ("rot", "Luân chuyển", False, ["Mùa BTC", "Nghiêng BTC", "Cân bằng", "Nghiêng altcoin", "Mùa altcoin"],
     "Chỉ số mùa altcoin (bao nhiêu altcoin lớn thắng BTC trong 90 ngày) và 8 altcoin lớn nhất so với BTC 30 ngày."),
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


def coinmetrics(metrics: list, start: str = "2017-01-01") -> dict:
    """Coin Metrics Community API (miễn phí, không cần mã): {metric: {ngày: giá trị}}."""
    out = {m: {} for m in metrics}
    url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    params = {"assets": "btc", "metrics": ",".join(metrics), "frequency": "1d", "start_time": start, "page_size": 10000}
    for _ in range(10):
        j = _get(url, params, 60)
        for r in j.get("data", []):
            d = day_of(datetime.strptime(r["time"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            for m in metrics:
                if r.get(m) not in (None, ""):
                    out[m][d] = float(r[m])
        url, params = j.get("next_page_url"), None
        if not url:
            break
        time.sleep(0.6)
    if not any(out.values()):
        raise RuntimeError("Coin Metrics: không có dữ liệu")
    return {m: v for m, v in out.items() if v}


def dvol() -> dict:
    """Chỉ số độ biến động kỳ vọng BTC của Deribit theo ngày."""
    out, end = {}, int(time.time() * 1000)
    start_all = int(datetime(2021, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for _ in range(8):
        st = max(start_all, end - 900 * DAY * 1000)
        j = _get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                 {"currency": "BTC", "start_timestamp": st, "end_timestamp": end, "resolution": "1D"}, 40)
        rows = (j.get("result") or {}).get("data") or []
        for r in rows:
            out[day_of(r[0] / 1000)] = float(r[4])
        if st <= start_all or not rows:
            break
        end = st - 1
        time.sleep(0.3)
    if len(out) < 100:
        raise RuntimeError("DVOL: thiếu dữ liệu")
    return out


def alt_closes(errs: dict) -> dict:
    """Giá đóng cửa ngày của rổ altcoin; lưu đệm ở data/compass_cache.json, mỗi ngày chỉ tải thêm đoạn mới."""
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    al = cache.get("alts") or {}
    since = datetime(2020, 6, 1, tzinfo=timezone.utc).timestamp()
    fails, t0, skipped = [], time.time(), 0
    for a in ALTS_ALL:
        old = {int(k): v for k, v in (al.get(a) or {}).items()}
        if not old and time.time() - t0 > 180:      # lần đầu tải toàn bộ lịch sử khá lâu: chia ra nhiều ngày
            skipped += 1
            continue
        try:
            new = okx_daily(a, (max(old) - 5 * DAY) if old else since)
            old.update(new)
        except Exception as ex:  # noqa: BLE001
            fails.append(f"{a}: {str(ex)[:40]}")
        if old:
            al[a] = {str(k): v for k, v in sorted(old.items())}
        time.sleep(0.1)
    if fails:
        errs["okx_alts"] = "; ".join(fails)[:200]
    if skipped:
        errs["okx_alts_later"] = f"{skipped} altcoin sẽ tải ở lần tính sau"
    cache["alts"] = al
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))
    return {a: {int(k): v for k, v in s.items()} for a, s in al.items() if len(s) > 100}


def bgeo(names: list, errs: dict) -> dict:
    """BGeometrics (bitcoin-data.com) miễn phí, không cần mã: giới hạn 15 lượt/ngày nên mỗi chỉ số tải tối đa 1 lần/ngày,
    lần đầu lấy từ 2022, sau đó chỉ lấy đoạn mới; lưu đệm ở data/compass_cache.json."""
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    bg = cache.get("bgeo") or {}
    now = time.time()
    for n in names:
        ent = bg.get(n) or {}
        data = ent.get("data") or {}
        if now - ent.get("t", 0) < 20 * 3600 and data:
            continue
        start = (datetime.strptime(max(data), "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d") if data else "2022-12-01"
        try:
            r = requests.get(f"https://bitcoin-data.com/v1/{n}", params={"startday": start},
                             headers={"User-Agent": UA, "Accept": "application/json"}, timeout=60)
            if r.status_code == 429:
                raise RuntimeError("hết lượt miễn phí hôm nay (429)")
            r.raise_for_status()
            rows = r.json()
            if isinstance(rows, dict):
                rows = rows.get("data") or [rows]
            for row in rows:
                d = row.get("d") or row.get("theDay")
                if not d:
                    continue
                vals = {}
                for k, v in row.items():
                    if k in ("d", "theDay", "unixTs") or isinstance(v, bool):
                        continue
                    try:
                        vals[k] = float(v)
                    except (TypeError, ValueError):
                        pass
                if vals:
                    data[d[:10]] = vals if len(vals) > 1 else next(iter(vals.values()))
            ent = {"t": now, "data": dict(sorted(data.items()))}
        except Exception as ex:  # noqa: BLE001
            errs["bgeo_" + n] = str(ex)[:120]
            ent["data"] = data
        bg[n] = ent
        time.sleep(2)
    cache["bgeo"] = bg
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))
    out = {}
    for n in names:
        dd = (bg.get(n) or {}).get("data") or {}
        out[n] = {day_of(datetime.strptime(k, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()): v for k, v in dd.items()}
    return out


def spot_net_buy(errs: dict) -> dict:
    """Lực mua spot ròng (mua chủ động trừ bán chủ động, USD/ngày) gộp các cặp BTC spot lớn, từ Coinalyze."""
    import market  # noqa: PLC0415
    if not market.COINALYZE_KEY:
        return {}
    ex = {e["code"]: e["name"] for e in market.cz_get("exchanges")}
    time.sleep(1.5)
    want = ("binance", "coinbase", "okx", "bybit", "kraken", "bitstamp")
    mk = [m for m in market.cz_get("spot-markets") if (m.get("base_asset") or "").upper() == "BTC"
          and (m.get("quote_asset") or "").upper() in ("USDT", "USD", "USDC")
          and any(w in ex.get(m.get("exchange"), "").lower() for w in want)]
    syms = [m["symbol"] for m in mk][:10]
    if not syms:
        raise RuntimeError("không có cặp spot BTC")
    time.sleep(1.5)
    rows = market.cz_get("ohlcv-history", {"symbols": ",".join(syms), "interval": "daily",
                                            "from": int(datetime(2022, 6, 1, tzinfo=timezone.utc).timestamp()), "to": int(time.time())})
    net: dict = {}
    have_bv = False
    for row in rows:
        for h in row.get("history", []):
            if h.get("bv") is None or not h.get("v"):
                continue
            have_bv = True
            d = day_of(h["t"])
            net[d] = net.get(d, 0.0) + (2 * float(h["bv"]) - float(h["v"])) * float(h.get("c") or 0)
    if not have_bv:
        raise RuntimeError("Coinalyze không trả khối lượng mua (bv) cho spot")
    return net


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
        score = {d: v for d, v in score.items() if d <= last_px}     # bỏ ngày "tương lai" (Google Trends theo tuần, nến hôm nay chưa đóng)
        if score:
            IND[key] = (lens, name, score, raw, fmt)

    # Vĩ mô (đúng 3 thứ Glassnode nêu: đô la so với MA200, đường cong lợi suất, kỳ vọng lãi suất Fed)
    usd = ffill(safe("fred_usd", lambda: fred("DTWEXBGS")), last_px)
    curve = ffill(safe("fred_curve", lambda: fred("T10Y2Y")), last_px)
    y2 = ffill(safe("fred_y2", lambda: fred("DGS2")), last_px)
    ff = ffill(safe("fred_ff", lambda: fred("DFF")), last_px)
    if usd:
        m = sma(usd, 200)
        r = {d: (usd[d] / m[d] - 1) * 100 for d in m}
        add("macro", "usd", "Đồng đô la so với trung bình 200 ngày", rank(r, invert=True), r, "pct")
    if curve:
        add("macro", "curve", "Đường cong lợi suất (10 năm trừ 2 năm)", rank(curve), curve, "pp2")
    if y2 and ff:
        pp = {d: y2[d] - ff[d] for d in y2 if d in ff}
        add("macro", "path", "Kỳ vọng lãi suất Fed (lợi suất 2 năm trừ lãi suất Fed)", rank(pp, invert=True), pp, "pp2")

    # Dữ liệu on-chain miễn phí của Coin Metrics (cung trên sàn, tiền vào/ra sàn, số địa chỉ có số dư, MVRV)
    bg = safe("bgeo", lambda: bgeo(["hodl-waves-supply", "realized-profit", "realized-loss", "sth-mvrv"], errs))
    cm = safe("coinmetrics", lambda: coinmetrics(["SplyExNtv", "FlowInExNtv", "FlowOutExNtv", "AdrBalCnt", "CapMVRVCur", "PriceUSD", "SplyCur"]))

    # Dòng tiền: stablecoin 30 ngày, số dư trên sàn, lực mua spot, ETF Mỹ
    if stab:
        st = ffill(stab, last_px)
        c = chg(st, 30)
        add("flows", "stable", "Nguồn stablecoin (thay đổi 30 ngày)", rank(c), c, "pct")
    if cm.get("SplyExNtv"):
        c = chg(ffill(cm["SplyExNtv"], last_px, 5), 30)
        add("flows", "exbal", "BTC nằm trên sàn (thay đổi 30 ngày; giảm là tốt)", rank(c, invert=True), c, "pct")
    snb = safe("spot_net_buy", lambda: spot_net_buy(errs))
    if snb:
        s30 = {d: v / 1e6 for d, v in sma(snb, 30).items()}
        add("flows", "spot", "Lực mua spot ròng (mua chủ động trừ bán, trung bình 30 ngày, Coinalyze)", rank(s30, min_n=120), s30, "musd")
    elif prem_daily:
        p7 = sma(prem_daily, 7)
        add("flows", "prem", "Lực mua spot của người Mỹ (Coinbase Premium 7 ngày)", rank(p7), p7, "pct3")
    etf = etf_daily()
    if etf:
        ds = sorted(etf)
        s20 = {ds[i]: sum(etf[x] for x in ds[i - 19:i + 1]) for i in range(19, len(ds))}
        s20 = ffill(s20, last_px, 4)
        add("flows", "etf", "ETF Bitcoin ở Mỹ (tổng 20 phiên)", rank(s20, min_n=120), s20, "musd")

    # Hành vi nhà đầu tư: như Glassnode, tỷ lệ cung của người giữ dài hạn và mức thay đổi (BGeometrics, lượng cung theo tuổi coin)
    hw = bg.get("hodl-waves-supply") or {}
    if hw:
        old = ("age_6m_1y", "age_1y_2y", "age_2y_3y", "age_3y_4y", "age_4y_5y", "age_5y_7y", "age_7y_10y", "age_10y")
        share = {d: 100 * sum(v.get(k, 0) for k in old) / sum(v.values()) for d, v in hw.items() if isinstance(v, dict) and sum(v.values())}
        lth = {d: sum(v.get(k, 0) for k in old) for d, v in hw.items() if isinstance(v, dict)}
        share, lth = ffill(share, last_px, 7), ffill(lth, last_px, 7)
        add("behav", "lthshare", "Tỷ lệ cung của người giữ dài hạn (coin trên 6 tháng)", rank(share, min_n=365), share, "pct_lvl")
        c = chg(lth, 30, pct=False)
        add("behav", "lthchg", "Người giữ dài hạn gom thêm / bán ra (30 ngày)", rank(c, min_n=365), c, "btcs")
    sm = bg.get("sth-mvrv") or {}
    if sm:
        smf = ffill(sm, last_px, 12)    # bản miễn phí trễ khoảng 7 ngày
        add("behav", "sthmvrv", "MVRV của người giữ ngắn hạn (bản miễn phí trễ ~7 ngày)", rank(smf, min_n=365), smf, "x")
    if hw or sm:
        pass
    elif cm.get("FlowInExNtv") and cm.get("FlowOutExNtv"):
        net = {d: cm["FlowOutExNtv"][d] - cm["FlowInExNtv"][d] for d in cm["FlowOutExNtv"] if d in cm["FlowInExNtv"]}
        n30 = sma(ffill(net, last_px, 5), 30)
        add("behav", "netout", "BTC rút ròng khỏi sàn mỗi ngày (trung bình 30 ngày; dương là gom)", rank(n30), n30, "btcd")
    if not (hw or sm) and cm.get("AdrBalCnt"):
        c = chg(ffill(cm["AdrBalCnt"], last_px, 5), 30)
        add("behav", "holders", "Số địa chỉ có BTC (thay đổi 30 ngày)", rank(c), c, "pct2")
    if fng and not (hw or sm):
        f7a = sma(ffill(fng, last_px), 7)
        add("behav", "fng", "Fear & Greed (trung bình 7 ngày)", rank(f7a), f7a, "int")

    # Nền tảng on-chain: đà (trung bình 30 ngày so với 90 ngày) của phí, mức tham gia, thanh toán, độ đầy khối
    for key, chart, name in (("fees", "transaction-fees-usd", "Đà phí mạng"),
                             ("addr", "n-unique-addresses", "Đà địa chỉ hoạt động (mức tham gia)"),
                             ("vol", "estimated-transaction-volume-usd", "Đà khối lượng chuyển trên chuỗi (thanh toán)"),
                             ("block", "avg-block-size", "Đà độ đầy khối"),
                             ("tx", "n-transactions", "Đà số giao dịch")):
        s = safe("chain_" + key, lambda c=chart: chain(c))
        if not s:
            continue
        s = ffill(s, last_px, 5)
        a, b = sma(s, 30), sma(s, 90)
        mo = {d: a[d] / b[d] for d in b if b[d]}
        add("fund", key, name + " (30 ngày / 90 ngày)", rank(mo), mo, "x3")
        time.sleep(1)

    # Vị trí chu kỳ: giá so với giá vốn thực (MVRV) và các đường dài hạn
    p = dict(px)
    if cm.get("CapMVRVCur") and cm.get("PriceUSD"):
        rp = {d: cm["PriceUSD"][d] / v for d, v in cm["CapMVRVCur"].items() if v and cm["PriceUSD"].get(d)}
        rpf = ffill(rp, last_px, 75)      # giá vốn thực đổi chậm; bản miễn phí trễ khoảng 1 tháng nên giữ giá trị gần nhất
        mv = {d: p[d] / rpf[d] for d in p if d in rpf}
        add("cycle", "mvrv", f"Giá / giá vốn thực trên chuỗi (giá vốn khoảng {rpf[max(rpf)]:,.0f} $)".replace(",", "."), rank(mv, None, 365), mv, "x")
    rpf, rlf = bg.get("realized-profit") or {}, bg.get("realized-loss") or {}
    if rpf and rlf:
        a, b = sma(ffill(rpf, last_px, 5), 7), sma(ffill(rlf, last_px, 5), 7)
        rpl = {d: a[d] / b[d] for d in a if b.get(d)}
        add("cycle", "rpl", "Tỷ lệ lãi / lỗ đã chốt trên chuỗi (trung bình 7 ngày)", rank(rpl, None, 365), rpl, "x")
    else:
        m200 = sma(p, 200)
        mayer = {d: p[d] / m200[d] for d in m200}
        add("cycle", "mayer", "Giá / trung bình 200 ngày", rank(mayer, None, 365), mayer, "x")
        w200 = sma(p, 1400)
        r200w = {d: p[d] / w200[d] for d in w200}
        add("cycle", "w200", "Giá / trung bình 200 tuần", rank(r200w, None, 120), r200w, "x")

    # Phái sinh: funding, open interest so với vốn hoá, độ biến động kỳ vọng (DVOL)
    if cz.get("funding"):
        fd = sma(cz["funding"], 7)
        add("deriv", "funding", "Funding (trung bình 7 ngày)", rank(fd), {d: v * 100 for d, v in fd.items()}, "pct4")
    sply = ffill(cm.get("SplyCur") or {}, last_px, 40)
    oi_m = {d: v / (px[d] * (sply.get(d) or 19.9e6)) * 100 for d, v in (cz.get("oi_usd") or {}).items() if px.get(d)}
    if oi_m:
        add("deriv", "oi", "Open interest so với vốn hoá BTC", rank(oi_m, min_n=120), oi_m, "pct2l")
    dv = safe("dvol", dvol)
    if dv:
        add("deriv", "dvol", "Độ biến động kỳ vọng DVOL (Deribit)", rank(ffill(dv, last_px, 3)), dv, "int")

    # Luân chuyển: chỉ số mùa altcoin (bao nhiêu altcoin lớn thắng BTC trong 90 ngày) + altcoin lớn so với BTC 30 ngày
    alts = safe("okx_alts", lambda: alt_closes(errs))
    if alts:
        b90, b30 = chg(p, 90), chg(p, 30)
        season, rel = {}, {}
        for d in b90:
            xs = [chg_one(alts[a], d, 90) for a in alts]
            xs = [x for x in xs if x is not None]
            if len(xs) >= 12:
                season[d] = 100 * sum(1 for x in xs if x > b90[d]) / len(xs)
        for d in b30:
            xs = [chg_one(alts[a], d, 30) - b30[d] for a in ALTS if a in alts and chg_one(alts[a], d, 30) is not None]
            if len(xs) >= 4:
                rel[d] = statistics.median(xs)
        if season:
            add("rot", "season", f"Mùa altcoin: % trong {len(alts)} altcoin thắng BTC 90 ngày", rank(season), season, "int")
        if rel:
            add("rot", "alt30", f"{len(ALTS)} altcoin lớn nhất so với BTC (trung vị, 30 ngày)", rank(rel), rel, "pp")

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
    # Điểm tổng: như Glassnode, trung bình 4 nhóm rồi xếp hạng so với lịch sử nhiều năm của chính nó
    hmean = head
    head = rank(hmean, None, 365)
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
        if kind == "pct2":
            return f"{v:+.2f}%"
        if kind == "pct2l":
            return f"{v:.2f}%"
        if kind == "pp2":
            return f"{v:+.2f} điểm %"
        if kind == "x3":
            return f"{v:.3f}"
        if kind == "btcs":
            return f"{v:+,.0f} BTC"
        if kind == "btcd":
            return f"{v:+,.0f} BTC/ngày"
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
    conv = max(0, min(100, round(100 - 3 * sd)))
    conv_lbl = "Đồng thuận" if conv >= 70 else "Lẫn lộn" if conv >= 50 else "Lệch nhau"

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

    KEYS = ("macro", "flows", "behav", "fund", "cycle", "deriv", "rot")
    series = []
    for d in sorted(head):
        if d >= last - 730 * DAY:
            series.append([d * 1000, round(head[d])] + [round(L[k][d]) if d in L.get(k, {}) else None for k in KEYS])
    # So với Glassnode (số chép tay từ ảnh): mình lệch bao nhiêu điểm ở từng ô
    ref, mae = [], {}
    try:
        rows = json.load(open(os.path.join(DATA, "glassnode_ref.json"), encoding="utf-8")).get("rows") or []
    except (OSError, json.JSONDecodeError):
        rows = []
    for r in rows:
        d = day_of(datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        dd = max((x for x in head if x <= d), default=None)
        if dd is None:
            continue
        ours = {"head": round(head[dd]), **{k: round(L[k][dd]) for k in KEYS if dd in L.get(k, {})}}
        ref.append({"date": r["date"], "gn": {k: r.get(k) for k in ("head",) + KEYS}, "ours": ours})
        for k, v in ours.items():
            if r.get(k) is not None:
                mae.setdefault(k, []).append(abs(v - r[k]))
    mae = {k: round(sum(v) / len(v)) for k, v in mae.items()}
    hv = head[last]
    out = {"v": COMPASS_V, "asof": datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%d"),
           "head": {"score": round(hv), "band": band(hv, HEAD_BANDS), "bands": HEAD_BANDS, "d7": d7(head), "n_main": len(mains), "mean": round(hmean[last])},
           "conviction": {"v": conv, "label": conv_lbl}, "lenses": lenses, "bt": bt, "bt_all": allb,
           "bt_from": datetime.fromtimestamp(min(head), timezone.utc).strftime("%Y-%m-%d"),
           "series": series, "ref": ref, "ref_mae": mae, "errors": errs, "runtime_sec": round(time.time() - t0, 1)}
    return out


def chg_one(s: dict, d: int, n: int):
    a, b = s.get(d), s.get(d - n * DAY)
    return (a / b - 1) * 100 if a and b else None
