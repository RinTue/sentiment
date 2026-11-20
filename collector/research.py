"""Nghiên cứu dự báo xu hướng chính BTC: dựng bảng chỉ số theo ngày (không nhìn trước tương lai), sàng lọc từng chỉ số
và từng nhóm bằng kiểm tra cuốn chiếu (walk-forward), so với mốc "lúc nào cũng đoán tăng".

Chạy riêng bằng workflow "Nghiên cứu dự báo" (bấm tay hoặc mỗi tuần), ghi data/research.json và data/research_features.csv.
Không bao giờ làm hỏng lần chạy: nguồn nào lỗi thì bỏ nhóm đó và ghi lỗi.

Quy tắc chống nhìn trước: mỗi chỉ số được dời theo độ trễ công bố thật (FRED ngày +1, số dư Fed tuần +2, M2 tháng +35,
báo cáo COT +3, ETF +1, on-chain +1); mọi phép tính cuộn chỉ dùng quá khứ; khi học bỏ h ngày cuối của tập học
(nhãn của chúng chưa biết lúc dự báo).
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compass  # noqa: E402
import history_test as ht  # noqa: E402
import market  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "research.json")
CSV = os.path.join(DATA, "research_features.csv")
TEST_FROM = "2019-01-01"
SPLIT = "2023-01-01"          # trước: học/kiểm; từ đây: kiểm tra ngoài mẫu cho hệ số tương quan
HORIZONS = (30, 90)

GROUPS = {
    "price": "Cấu trúc giá", "liq": "Thanh khoản", "macro": "Vĩ mô", "cot": "Cá mập CME (COT)",
    "inst": "Tổ chức (ETF, Coinbase)", "micro": "Vi mô và phái sinh", "chain": "On-chain", "mood": "Tâm lý",
}
FEAT: dict = {}            # key -> (nhóm, tên, Series theo ngày)
ERR: dict = {}


def S(d: dict) -> pd.Series:
    """{giây UTC: giá trị} -> Series theo ngày."""
    if not d:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(int(k), unit="s").normalize(): float(v) for k, v in d.items() if v is not None})
    return s[~s.index.duplicated()].sort_index()


def daily(s: pd.Series, idx: pd.DatetimeIndex, lag: int = 1, gap: int = 10) -> pd.Series:
    """Đưa về lưới ngày, dời theo độ trễ công bố, lấp tối đa `gap` ngày bằng giá trị trước đó."""
    if s.empty:
        return pd.Series(index=idx, dtype=float)
    s = s.copy()
    s.index = s.index + pd.Timedelta(days=lag)
    return s.reindex(s.index.union(idx)).ffill(limit=gap).reindex(idx)


def add(group, key, name, s):
    s = s.replace([np.inf, -np.inf], np.nan)
    if s.notna().sum() > 400:
        FEAT[key] = (group, name, s)


def safe(name, fn, default=None):
    try:
        return fn()
    except Exception as ex:  # noqa: BLE001
        ERR[name] = str(ex)[:160]
        return default if default is not None else {}


# ---------------------------------------------------------------- nguồn bổ sung
def cot() -> pd.DataFrame:
    """Báo cáo Traders in Financial Futures của CFTC cho hợp đồng Bitcoin CME (miễn phí, mỗi tuần)."""
    url = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
    q = {"$where": "market_and_exchange_names like 'BITCOIN - CHICAGO MERCANTILE EXCHANGE%'",
         "$order": "report_date_as_yyyy_mm_dd", "$limit": 5000}
    rows = compass._get(url, q, 60)
    if not rows:
        raise RuntimeError("COT: không có dữ liệu")
    df = pd.DataFrame(rows)
    df["d"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"].str[:10])
    num = lambda c: pd.to_numeric(df[c], errors="coerce")  # noqa: E731
    oi = num("open_interest_all")
    out = pd.DataFrame({"lev": (num("lev_money_positions_long") - num("lev_money_positions_short")) / oi,
                        "am": (num("asset_mgr_positions_long") - num("asset_mgr_positions_short")) / oi,
                        "oi": oi}).set_index(df["d"])
    return out[~out.index.duplicated()].sort_index()


def cz_volume(spot: bool) -> pd.DataFrame:
    """Khối lượng (USD) và khối lượng mua chủ động theo ngày, gộp các cặp BTC lớn trên Coinalyze."""
    if not market.COINALYZE_KEY:
        raise RuntimeError("chưa có COINALYZE_API_KEY")
    ex = {e["code"]: e["name"] for e in market.cz_get("exchanges")}
    time.sleep(1.5)
    want = ("binance", "coinbase", "okx", "bybit", "kraken", "bitstamp")
    if spot:
        mk = [m for m in market.cz_get("spot-markets") if (m.get("base_asset") or "").upper() == "BTC"
              and (m.get("quote_asset") or "").upper() in ("USDT", "USD", "USDC")]
    else:
        mk = [m for m in market.cz_get("future-markets") if (m.get("base_asset") or "").upper() == "BTC" and m.get("is_perpetual")
              and (m.get("quote_asset") or "").upper() in ("USDT", "USDC")]      # chỉ hợp đồng tuyến tính: khối lượng tính bằng BTC
    syms = [m["symbol"] for m in mk if any(w in ex.get(m.get("exchange"), "").lower() for w in want)][:10]
    time.sleep(1.5)
    rows = market.cz_get("ohlcv-history", {"symbols": ",".join(syms), "interval": "daily",
                                           "from": int(datetime(2019, 6, 1, tzinfo=timezone.utc).timestamp()), "to": int(time.time())})
    agg: dict = {}
    for row in rows:
        for h in row.get("history", []):
            if not h.get("v"):
                continue
            c = float(h.get("c") or 0)
            v, bv = float(h["v"]), float(h.get("bv") or 0)
            k = c
            a = agg.setdefault(pd.Timestamp(int(h["t"]), unit="s").normalize(), [0.0, 0.0])
            a[0] += v * k
            a[1] += bv * k
    df = pd.DataFrame(agg, index=["v", "bv"]).T.sort_index()
    if df.empty:
        raise RuntimeError("Coinalyze: không có khối lượng")
    return df


# ---------------------------------------------------------------- dựng bảng chỉ số
def build() -> tuple[pd.DataFrame, pd.Series]:
    J = json.load(open(os.path.join(DATA, "cycles.json"), encoding="utf-8"))
    px = pd.Series(J["close"], index=pd.date_range(J["start"], periods=len(J["close"]), freq="D"), dtype=float).ffill()
    px = px[px.index >= "2013-01-01"]
    idx = px.index
    lp = np.log(px)

    # Cấu trúc giá
    add("price", "dd", "Cách đỉnh cao nhất", lp - lp.cummax())
    add("price", "w200", "Giá / trung bình 200 tuần", lp - np.log(px.rolling(1400).mean()))
    add("price", "d200", "Giá / trung bình 200 ngày", lp - np.log(px.rolling(200).mean()))
    add("price", "r90", "Lãi/lỗ 90 ngày", lp - lp.shift(90))
    add("price", "r365", "Lãi/lỗ 1 năm", lp - lp.shift(365))
    add("price", "vol30", "Độ biến động 30 ngày", lp.diff().rolling(30).std())
    add("price", "range60", "Độ rộng biên giá 60 ngày (nén = thấp)", np.log(px.rolling(60).max() / px.rolling(60).min()))

    # Thanh khoản và vĩ mô (FRED)
    fr = lambda sid: S(safe("fred_" + sid, lambda: compass.fred(sid, "2012-01-01")))  # noqa: E731
    walcl, tga, rrp = fr("WALCL"), fr("WTREGEN"), fr("RRPONTSYD")
    if not walcl.empty and not tga.empty:
        rr = daily(rrp, idx, 1, 10).fillna(0)
        nl = daily(walcl, idx, 2, 10) / 1000 - daily(tga, idx, 2, 10) / 1000 - rr
        add("liq", "netliq", "Thanh khoản ròng của Fed (thay đổi 13 tuần)", np.log(nl / nl.shift(91)))
    m2 = fr("M2SL")
    if not m2.empty:
        m = daily(m2, idx, 35, 40)
        add("liq", "m2", "Cung tiền M2 Mỹ (thay đổi 6 tháng)", np.log(m / m.shift(182)))
    usd = daily(fr("DTWEXBGS"), idx)
    if usd.notna().any():
        add("liq", "usd", "Đồng đô la (thay đổi 3 tháng)", np.log(usd / usd.shift(63)))
    stab = daily(S(safe("stable", ht.stable_hist)), idx, 1, 5)
    add("liq", "stable", "Nguồn stablecoin (thay đổi 30 ngày)", np.log(stab / stab.shift(30)))
    y2, ff = daily(fr("DGS2"), idx), daily(fr("DFF"), idx)
    add("macro", "path", "Kỳ vọng lãi suất Fed (2 năm − lãi suất Fed)", y2 - ff)
    real = daily(fr("DFII10"), idx)
    add("macro", "real", "Lợi suất thực 10 năm (thay đổi 3 tháng)", real - real.shift(63))
    add("macro", "hy", "Chênh lệch lãi suất trái phiếu rủi ro", daily(fr("BAMLH0A0HYM2"), idx))
    ndx = daily(fr("NASDAQCOM"), idx)
    add("macro", "ndx", "Nasdaq so với trung bình 200 ngày", np.log(ndx / ndx.rolling(200, min_periods=150).mean()))
    add("macro", "vix", "Chỉ số sợ hãi chứng khoán VIX", daily(fr("VIXCLS"), idx))
    add("macro", "curve", "Đường cong lợi suất 10 năm − 2 năm", daily(fr("T10Y2Y"), idx))

    # Cá mập CME
    c = safe("cot", cot, pd.DataFrame())
    if isinstance(c, pd.DataFrame) and not c.empty:
        lev, am = daily(c["lev"], idx, 3, 10), daily(c["am"], idx, 3, 10)
        add("cot", "cot_lev", "Quỹ đầu cơ CME: long − short (theo OI)", lev)
        add("cot", "cot_am", "Quỹ quản lý tài sản CME: long − short", am)
        add("cot", "cot_lev4", "Quỹ đầu cơ CME: thay đổi 4 tuần", lev - lev.shift(28))
        add("cot", "cot_am4", "Quỹ quản lý tài sản CME: thay đổi 4 tuần", am - am.shift(28))

    # Tổ chức: ETF, Coinbase Premium
    etf = S(compass.etf_daily())
    if not etf.empty:
        e20 = etf.rolling(20).sum()
        add("inst", "etf20", "ETF Bitcoin Mỹ (tổng 20 phiên)", daily(e20, idx, 1, 4))
    okx = S(safe("okx_spot", ht.okx_spot_daily))
    btcd = S(ht.btc_close())
    if not okx.empty:
        prem = (btcd / okx - 1) * 100
        add("inst", "prem", "Coinbase Premium (7 ngày)", daily(prem.rolling(7).mean(), idx, 1, 3))

    # Vi mô và phái sinh
    cz = safe("coinalyze", ht.coinalyze_daily)
    if cz:
        fd = S(cz.get("funding") or {})
        add("micro", "funding", "Funding (7 ngày)", daily(fd.rolling(7).mean(), idx, 1, 3))
        oi = S(cz.get("oi_usd") or {})
        oim = daily(oi, idx, 1, 3) / px
        add("micro", "oi", "Open interest so với giá (mức đòn bẩy)", np.log(oim / oim.rolling(365, min_periods=200).mean()))
        add("micro", "oi30", "Open interest thay đổi 30 ngày", np.log(oim / oim.shift(30)))
        lq = cz.get("liq") or {}
        if lq:
            l = pd.Series({pd.Timestamp(int(k), unit="s").normalize(): v[0] for k, v in lq.items()}).sort_index()
            s_ = pd.Series({pd.Timestamp(int(k), unit="s").normalize(): v[1] for k, v in lq.items()}).sort_index()
            add("micro", "liq7", "Thanh lý 7 ngày: short so với long", daily(np.log((s_.rolling(7).sum() + 1e5) / (l.rolling(7).sum() + 1e5)), idx, 1, 3))
    sp = safe("cz_spot", lambda: cz_volume(True), pd.DataFrame())
    pe = safe("cz_perp", lambda: cz_volume(False), pd.DataFrame())
    if isinstance(sp, pd.DataFrame) and not sp.empty:
        net = (2 * sp["bv"] - sp["v"])
        add("micro", "spotnet", "Lực mua spot ròng (30 ngày, so với khối lượng)", daily(net.rolling(30).sum() / sp["v"].rolling(30).sum(), idx, 1, 3))
        if isinstance(pe, pd.DataFrame) and not pe.empty:
            ratio = (sp["v"].rolling(30).sum() / pe["v"].rolling(30).sum())
            add("micro", "spotperp", "Khối lượng spot so với hợp đồng vĩnh cửu (30 ngày)", daily(np.log(ratio), idx, 1, 3))
            pnet = (2 * pe["bv"] - pe["v"]).rolling(30).sum() / pe["v"].rolling(30).sum()
            add("micro", "cvdgap", "Lệch pha: mua ròng spot − mua ròng phái sinh", daily(net.rolling(30).sum() / sp["v"].rolling(30).sum() - pnet, idx, 1, 3))
    dv = S(safe("dvol", compass.dvol))
    add("micro", "dvol", "Độ biến động kỳ vọng DVOL", daily(dv, idx, 1, 3))

    # On-chain
    cm = safe("coinmetrics", lambda: compass.coinmetrics(["SplyExNtv", "FlowInExNtv", "FlowOutExNtv", "CapMVRVCur"], "2014-01-01"))
    cm.update(safe("coinmetrics_whales", lambda: compass.coinmetrics(["AdrBalNtv1KCnt"], "2014-01-01")) or {})
    if cm:
        g = lambda k, lag=1, gap=5: daily(S(cm.get(k) or {}), idx, lag, gap)  # noqa: E731
        ex = g("SplyExNtv")
        add("chain", "exbal", "BTC trên sàn (thay đổi 30 ngày)", np.log(ex / ex.shift(30)))
        nout = (g("FlowOutExNtv") - g("FlowInExNtv")).rolling(30).mean()
        add("chain", "netout", "BTC rút ròng khỏi sàn (30 ngày)", nout)
        w = g("AdrBalNtv1KCnt")
        add("chain", "whales", "Số ví từ 1.000 BTC (thay đổi 30 ngày)", np.log(w / w.shift(30)))
        mv = g("CapMVRVCur", 1, 60)    # bản miễn phí trễ khoảng 1 tháng: chỉ dùng số đã công bố
        add("chain", "mvrv", "MVRV (giá / giá vốn thực)", np.log(mv))
    bg = safe("bgeo", lambda: compass.bgeo(["hodl-waves-supply", "realized-profit", "realized-loss", "sth-mvrv"], ERR))
    if bg:
        sm = S(bg.get("sth-mvrv") or {})
        add("chain", "sthmvrv", "MVRV người giữ ngắn hạn", daily(np.log(sm), idx, 8, 12))
        rp, rl = S(bg.get("realized-profit") or {}), S(bg.get("realized-loss") or {})
        if not rp.empty and not rl.empty:
            add("chain", "rpl", "Tỷ lệ lãi / lỗ đã chốt (7 ngày)", daily(np.log(rp.rolling(7).mean() / rl.abs().rolling(7).mean()), idx, 1, 5))
        hw = bg.get("hodl-waves-supply") or {}
        old = ("age_6m_1y", "age_1y_2y", "age_2y_3y", "age_3y_4y", "age_4y_5y", "age_5y_7y", "age_7y_10y", "age_10y")
        sh = S({k: 100 * sum(v.get(o, 0) for o in old) / sum(v.values()) for k, v in hw.items() if isinstance(v, dict) and sum(v.values())})
        add("chain", "lth30", "Tỷ lệ cung người giữ dài hạn (thay đổi 30 ngày)", daily(sh - sh.shift(30), idx, 1, 7))

    # Tâm lý
    fg = S(safe("fng", ht.fng_hist))
    add("mood", "fng", "Fear & Greed (7 ngày)", daily(fg.rolling(7).mean(), idx, 1, 3))
    tr = S(ht.trends_daily("global", 0))
    add("mood", "trends", "Google: \"buy bitcoin\"", daily(tr, idx, 0, 10))

    X = pd.DataFrame({k: v[2] for k, v in FEAT.items()}, index=idx)
    return X, px


# ---------------------------------------------------------------- kiểm tra
def targets(px: pd.Series) -> dict:
    lp = np.log(px)
    return {h: (lp.shift(-h) - lp) for h in HORIZONS}


def walk(X: pd.DataFrame, y: pd.Series, h: int, cols: list, start: str = TEST_FROM, min_train: int = 540) -> pd.DataFrame:
    """Học lại mỗi tháng bằng hồi quy logistic có điều chuẩn, chỉ trên quá khứ đã biết nhãn; trả về dự báo ngoài mẫu."""
    from sklearn.linear_model import LogisticRegression
    yb = (y > 0).astype(float).where(y.notna())
    out = []
    for m in pd.date_range(start, X.index[-1], freq="MS"):
        tr = X.index < m - pd.Timedelta(days=h)
        D = X.loc[tr, cols].join(yb.rename("y")).dropna()
        if len(D) < min_train or D["y"].nunique() < 2:
            continue
        te = X.loc[(X.index >= m) & (X.index < m + pd.offsets.MonthBegin(1)), cols].dropna()
        if te.empty:
            continue
        mu, sd = D[cols].mean(), D[cols].std().replace(0, 1)
        mdl = LogisticRegression(C=0.3, max_iter=2000).fit((D[cols] - mu) / sd, D["y"])
        p = mdl.predict_proba((te - mu) / sd)[:, 1]
        out.append(pd.DataFrame({"p": p, "base": D["y"].mean()}, index=te.index))
    if not out:
        return pd.DataFrame()
    R = pd.concat(out).join(yb.rename("y")).dropna()
    return R


def score(R: pd.DataFrame) -> dict | None:
    if R is None or len(R) < 200:
        return None
    acc = float(((R.p > .5) == (R.y == 1)).mean())
    base = float(((R.base > .5) == (R.y == 1)).mean())       # mốc: đoán theo tỷ lệ tăng của quá khứ (thường là "luôn tăng")
    brier = float(((R.p - R.y) ** 2).mean())
    bb = float(((R.base - R.y) ** 2).mean())
    strong = R[(R.p > .6) | (R.p < .4)]
    acc_s = float(((strong.p > .5) == (strong.y == 1)).mean()) if len(strong) > 30 else None
    return {"n": int(len(R)), "from": str(R.index[0].date()), "acc": round(acc * 100, 1), "base": round(base * 100, 1),
            "edge": round((acc - base) * 100, 1), "skill": round((1 - brier / bb) * 100, 1),
            "acc_strong": round(acc_s * 100, 1) if acc_s is not None else None, "share_strong": round(len(strong) / len(R) * 100)}


def run() -> dict:
    t0 = time.time()
    X, px = build()
    Y = targets(px)
    X.round(6).to_csv(CSV, index_label="date")
    feats = []
    for k, (g, name, s) in FEAT.items():
        row = {"key": k, "group": g, "name": name, "from": str(s.first_valid_index().date()), "now": None}
        last = s.dropna()
        row["now"] = round(float(last.iloc[-1]), 5) if len(last) else None
        for h in HORIZONS:
            y = Y[h]
            D = pd.DataFrame({"x": s, "y": y}).dropna()
            tr, te = D[D.index < SPLIT], D[D.index >= SPLIT]
            row[f"rho{h}_tr"] = round(float(tr.x.corr(tr.y, method="spearman")), 3) if len(tr) > 200 else None
            row[f"rho{h}_te"] = round(float(te.x.corr(te.y, method="spearman")), 3) if len(te) > 200 else None
            row[f"wf{h}"] = score(walk(X, y, h, [k]))
        feats.append(row)
        print(k, row.get("wf90"), file=sys.stderr)

    # Chỉ số "qua vòng": thắng mốc ở cả 30 và 90 ngày (hoặc thắng rõ ở 90 ngày) và cùng chiều trước/sau 2023
    def passed(r):
        w30, w90 = r.get("wf30"), r.get("wf90")
        same = (r.get("rho90_tr") or 0) * (r.get("rho90_te") or 0) > 0
        # cần ít nhất ~2,5 năm kiểm tra ngoài mẫu: giai đoạn ngắn dễ cho kết quả đẹp do may
        return bool(w90 and w90["n"] >= 900 and w90["skill"] > 0 and w90["edge"] > 0 and same and (not w30 or w30["skill"] > -2))
    for r in feats:
        r["pass"] = passed(r)
        r["short"] = bool(not r.get("wf90") or r["wf90"]["n"] < 900)

    groups, models = {}, {}
    for g in GROUPS:
        cols = [r["key"] for r in feats if r["group"] == g and r["pass"]] or [r["key"] for r in feats if r["group"] == g]
        if cols:
            groups[g] = {"cols": cols, **{f"wf{h}": score(walk(X, Y[h], h, cols)) for h in HORIZONS}}
    good = [r["key"] for r in feats if r["pass"]]
    long_ = [k for k in good if FEAT[k][2].first_valid_index() <= pd.Timestamp("2017-06-01")]
    for name, cols in (("full", good), ("long", long_)):
        if cols:
            models[name] = {"cols": cols, **{f"wf{h}": score(walk(X, Y[h], h, cols)) for h in HORIZONS}}
    abl = {}
    if len(good) > 2 and models.get("full"):
        for g in GROUPS:
            rest = [k for k in good if FEAT[k][0] != g]
            if len(rest) < len(good) and rest:
                s90 = score(walk(X, Y[90], 90, rest))
                abl[g] = s90
    # Dự báo hôm nay bằng các chỉ số qua vòng (học trên toàn bộ quá khứ đã biết nhãn)
    now = {}
    from sklearn.linear_model import LogisticRegression
    for h in HORIZONS:
        for cols in (good, long_):
            if not cols:
                continue
            D = X[cols].join((Y[h] > 0).astype(float).where(Y[h].notna()).rename("y")).dropna()
            last = X[cols].dropna()
            if len(D) < 300 or last.empty:
                continue
            mu, sd = D[cols].mean(), D[cols].std().replace(0, 1)
            mdl = LogisticRegression(C=0.3, max_iter=2000).fit((D[cols] - mu) / sd, D["y"])
            z = ((last.iloc[-1] - mu) / sd)
            now[f"p{h}"] = round(float(mdl.predict_proba(z.to_frame().T)[0, 1]) * 100, 1)
            now[f"asof{h}"] = str(last.index[-1].date())
            now[f"contrib{h}"] = {c: round(float(mdl.coef_[0][i] * z[c]), 3) for i, c in enumerate(cols)}
            now[f"base{h}"] = round(float(D["y"].mean()) * 100, 1)
            break
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "test_from": TEST_FROM, "split": SPLIT,
           "groups_name": GROUPS, "features": feats, "groups": groups, "models": models, "ablation": abl, "now": now,
           "errors": ERR, "runtime_sec": round(time.time() - t0)}
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, float) and not np.isfinite(o):
            return None
        if isinstance(o, (np.floating, np.integer)):
            return clean(float(o))
        return o
    out = clean(out)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    try:
        r = run()
        print(json.dumps({"models": r["models"], "errors": r["errors"]}, ensure_ascii=False, default=float)[:3000])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        json.dump({"error": traceback.format_exc()[-1500:], "updated": datetime.now(timezone.utc).isoformat()},
                  open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    sys.exit(0)
