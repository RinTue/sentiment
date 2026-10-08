"""Cảnh báo qua Telegram: bản tin buổi sáng, vùng tâm lý cực đoan, funding bất thường, sự kiện vĩ mô cấp 1, lỗi bộ thu thập.

Cần hai secret trên GitHub: TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID. Thiếu thì bỏ qua, không lỗi.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE_FILE = os.path.join(DATA, "alerts_state.json")
CAL_CACHE = os.path.join(DATA, "calendar_week.json")
VN = timezone(timedelta(hours=7))
MAX_MARKET_PER_DAY = 3
ORDER = ["xf", "f", "n", "g", "xg"]
BAND_NAME = {"xf": "Sợ hãi cực độ", "f": "gần Sợ hãi cực độ", "n": "Trung tính", "g": "gần Tham lam cực độ", "xg": "Tham lam cực độ"}
TIER1 = [
    (r"^Core CPI", "CPI lõi"), (r"^CPI", "CPI (lạm phát)"), (r"Non-Farm Employment", "Bảng lương phi nông nghiệp (NFP)"),
    (r"Unemployment Rate", "Tỷ lệ thất nghiệp"), (r"Federal Funds Rate", "Fed công bố lãi suất"), (r"FOMC Statement", "Tuyên bố của Fed"),
    (r"FOMC Press Conference", "Họp báo của Fed"), (r"Core PCE", "PCE lõi"), (r"Fed Chair .*Speaks|Powell", "Chủ tịch Fed phát biểu"),
    (r"Advance GDP", "GDP sơ bộ"),
]


def vn(x: float | None, d: int = 0, sign: bool = False) -> str:
    if x is None:
        return "–"
    s = f"{x:+,.{d}f}" if sign else f"{x:,.{d}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def page_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def classify(v: float) -> str:
    return "xf" if v < 20 else "f" if v <= 25 else "xg" if v >= 80 else "g" if v >= 75 else "n"


def ci_band(v: float, prev: str | None) -> str:
    """Có độ trễ 3 điểm khi quay về trung tính, để không báo đi báo lại khi chỉ số dao động quanh ngưỡng."""
    b = classify(v)
    if prev is None or prev == b:
        return b
    pi, bi = ORDER.index(prev), ORDER.index(b)
    if pi < 2 and bi > pi:                     # phía sợ hãi, đang dịu đi
        nb = classify(v - 3)
        return nb if ORDER.index(nb) > pi else prev
    if pi > 2 and bi < pi:                     # phía tham lam, đang nguội đi
        nb = classify(v + 3)
        return nb if ORDER.index(nb) < pi else prev
    return b


def fund_band(p: float, prev: str | None) -> str:
    if p >= 95 or (prev == "hi" and p >= 90):
        return "hi"
    if p <= 5 or (prev == "lo" and p <= 10):
        return "lo"
    return "n"


def calendar() -> list:
    """Sự kiện Mỹ mức cao tuần này (ForexFactory), lưu bản gần nhất phòng khi nguồn lỗi."""
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        ev = r.json()
        with open(CAL_CACHE, "w", encoding="utf-8") as f:
            json.dump(ev, f)
    except Exception:  # noqa: BLE001
        try:
            with open(CAL_CACHE, encoding="utf-8") as f:
                ev = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
    out = []
    for e in ev:
        if e.get("country") != "USD" or e.get("impact") != "High":
            continue
        vi = next((v for p, v in TIER1 if re.search(p, e.get("title", ""), re.I)), None)
        if not vi:
            continue
        try:
            t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"t": t, "vi": vi, "en": e.get("title", ""), "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
    return sorted(out, key=lambda x: x["t"])


def send(token: str, chat: str, text: str) -> None:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
    if r.status_code != 200:
        # Không in phản hồi: nhật ký của kho công khai ai cũng đọc được.
        raise RuntimeError(f"Telegram trả lỗi {r.status_code}")


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def composite_rows() -> list:
    p = os.path.join(DATA, "composite.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            try:
                rows.append((datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00")).timestamp(), float(r["value"])))
            except (ValueError, KeyError, TypeError):
                continue
        return rows


def summary_lines(latest: dict) -> list:
    M = latest.get("market") or {}
    a, nw, so = latest.get("all") or {}, latest.get("news") or {}, latest.get("social") or {}
    L = []
    if a.get("index") is not None:
        L.append(f"• Văn bản: {a['index']}/100 (tin tức {nw.get('index', '–')}, mạng xã hội {so.get('index', '–')})")
    if M.get("fng"):
        L.append(f"• Fear &amp; Greed: {M['fng']['value']} ({html.escape(M['fng']['cls'])})")
    if M.get("funding"):
        L.append(f"• Funding BTC: phân vị {vn(M['funding']['pct'])}% trong 90 ngày ({M['funding']['src']})")
    if M.get("ls"):
        L.append(f"• Tài khoản đang long: {vn(M['ls']['buy'] * 100)}% ({M['ls']['src']})")
    if M.get("premium"):
        L.append(f"• Coinbase Premium 24h: {vn(M['premium']['avg24'], 3, True)}%")
    if M.get("stable") and M["stable"].get("ch30") is not None:
        L.append(f"• Stablecoin 30 ngày: {vn(M['stable']['ch30'], 1, True)}% ({vn(M['stable']['total'])} tỷ $)")
    dv = ((M.get("drivers") or {}).get("windows") or {}).get("24")
    if dv:
        L.append(f"• Đà giá 24h: {dv['label']} (giá {vn(dv['dp'], 1, True)}%, open interest {vn(dv['doi'], 1, True)}%)")
    mac = latest.get("macro") or {}
    if mac.get("label") and mac.get("have", 1):
        L.append(f"• Vĩ mô: {mac['label']} (điểm {mac.get('score', 0):+d})")
    if M.get("btc"):
        L.append(f"• BTC: {vn(M['btc']['price'])} $" + (f" (24h {vn(M['btc']['ch24'], 1, True)}%)" if M["btc"].get("ch24") is not None else ""))
    return L


ZONES = [(20, "Sợ hãi cực độ"), (40, "Sợ hãi"), (60, "Trung tính"), (80, "Tham lam"), (101, "Tham lam cực độ")]


def zone(v: float) -> str:
    return next(n for lim, n in ZONES if v < lim)


def run(latest: dict) -> dict:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    st = load_state()
    now = datetime.now(timezone.utc)
    vnow = now.astimezone(VN)
    vday = vnow.strftime("%Y-%m-%d")
    if st.get("day") != vday:
        st["day"], st["market_count"] = vday, 0
    st.setdefault("sent", {})
    st.setdefault("events", [])
    msgs: list[tuple[str, str]] = []     # (loại, nội dung)
    M = latest.get("market") or {}
    ci = (M.get("composite") or {}).get("value")
    url = page_url()
    rows = composite_rows()
    st.setdefault("bands", {})

    def recently(key: str, hours: float) -> bool:
        return time.time() - st["sent"].get(key, 0) < hours * 3600

    # 1) vùng cực đoan của chỉ số tổng hợp
    if ci is not None:
        prev = st["bands"].get("ci")
        b = ci_band(ci, prev)
        st["bands"]["ci"] = b
        if prev and b != prev:
            pi, bi = ORDER.index(prev), ORDER.index(b)
            if abs(bi - 2) > abs(pi - 2):
                head = f"⚠️ Tâm lý vào vùng <b>{BAND_NAME[b]}</b>: {ci}/100"
                tip = ("Vùng này thường gần các đáy ngắn hạn. Trader tâm lý chờ giá ngừng giảm rồi mới tính, không bắt dao rơi."
                       if b in ("xf", "f") else "Vùng này thường gần các đỉnh ngắn hạn. Trader tâm lý thường giảm đòn bẩy và chốt bớt.")
            else:
                head = f"ℹ️ Tâm lý rời vùng {BAND_NAME[prev]}, nay {ci}/100 ({zone(ci)})"
                tip = "Mức cực đoan đã dịu. Theo dõi xem giá có xác nhận hướng mới không."
            msgs.append(("market", f"{head}\n\n" + "\n".join(summary_lines(latest)) + f"\n\n{tip}"))

    # 2) thay đổi mạnh trong 24 giờ
    if ci is not None and rows:
        ref = [v for t, v in rows if t <= time.time() - 23 * 3600]
        if ref and abs(ci - ref[-1]) >= 15 and not recently("move", 12):
            d = ci - ref[-1]
            msgs.append(("market:move", f"{'📈' if d > 0 else '📉'} Tâm lý {'tăng' if d > 0 else 'giảm'} mạnh: {ref[-1]:.0f} → {ci} trong 24 giờ\n\n" + "\n".join(summary_lines(latest))))

    # 2b) giá BTC chạy mạnh: nói rõ đà đến từ đâu
    dv = ((M.get("drivers") or {}).get("windows") or {}).get("24")
    if dv and abs(dv["dp"]) >= 5 and not recently("drivers", 12):
        liq = (M.get("drivers") or {}).get("liq") or {}
        extra = f"\nThanh lý trên OKX ({liq.get('hours', 24):.0f} giờ): short {vn(liq.get('short', 0) / 1e6, 1)} triệu $, long {vn(liq.get('long', 0) / 1e6, 1)} triệu $." if liq else ""
        msgs.append(("market:drivers", f"{'🚀' if dv['dp'] > 0 else '🔻'} BTC {vn(dv['dp'], 1, True)}% trong 24 giờ: <b>{html.escape(dv['label'])}</b>\n\n{html.escape(dv['why'])}\n"
                     f"Open interest {vn(dv['doi'], 1, True)}% · mua spot ròng {vn(dv['spot_btc'], 0, True) if dv.get('spot_btc') is not None else '–'} BTC (OKX) · Coinbase Premium {vn(dv['prem'], 3, True) if dv.get('prem') is not None else '–'}%{extra}"))

    # 3) funding bất thường
    fd = M.get("funding")
    if fd:
        prev = st["bands"].get("funding")
        b = fund_band(fd["pct"], prev)
        st["bands"]["funding"] = b
        if prev and b != prev and b != "n":
            txt = ("Phe long đang rất đông và trả phí cao. Nếu giá giảm, các lệnh long dễ bị thanh lý dây chuyền."
                   if b == "hi" else "Phe short đang rất đông. Nếu giá tăng, short có thể bị ép mua lại (short squeeze).")
            msgs.append(("market:funding", f"⚠️ Funding BTC ở phân vị <b>{vn(fd['pct'])}%</b> của 90 ngày ({vn(fd['last'] * 100, 4, True)}% mỗi 8 giờ, {fd['src']})\n\n{txt}"))

    # 4) sự kiện vĩ mô cấp 1 trong 3 giờ tới
    try:
        cal = calendar()
    except Exception:  # noqa: BLE001
        cal = []
    soon = [e for e in cal if timedelta(0) < e["t"] - now <= timedelta(hours=3)]
    new_ev = [e for e in soon if f"{e['en']}|{e['t'].isoformat()}" not in st["events"]]
    if new_ev:
        lines = []
        for e in new_ev:
            st["events"].append(f"{e['en']}|{e['t'].isoformat()}")
            mins = int((e["t"] - now).total_seconds() // 60)
            extra = " · ".join(x for x in (f"dự báo {e['forecast']}" if e["forecast"] else "", f"kỳ trước {e['previous']}" if e["previous"] else "") if x)
            lines.append(f"• <b>{e['vi']}</b> lúc {e['t'].astimezone(VN):%H:%M} (còn {" ".join(x for x in (f"{mins // 60} giờ" if mins >= 60 else "", f"{mins % 60} phút" if mins % 60 or mins < 60 else "") if x)})" + (f"\n   {html.escape(extra)}" if extra else ""))
        msgs.append(("event", "🗓 Sắp có số liệu vĩ mô quan trọng của Mỹ:\n" + "\n".join(lines) + "\n\nBiến động BTC thường tăng mạnh vài phút quanh giờ công bố; cẩn thận với đòn bẩy và lệnh dừng lỗ quá sát."))
    st["events"] = st["events"][-60:]

    # 5) bộ thu thập thiếu dữ liệu
    issues = []
    if (latest.get("kept_count") or 0) < 40:
        issues.append(f"chỉ giữ được {latest.get('kept_count', 0)} bài")
    if ci is None or len((M.get("composite") or {}).get("parts", [])) < 4:
        issues.append(f"chỉ số tổng hợp chỉ có {len((M.get('composite') or {}).get('parts', []))} thành phần")
    mac = latest.get("macro") or {}
    if mac.get("error") or (mac.get("have") is not None and mac["have"] < 6):
        issues.append("thiếu nhiều số liệu vĩ mô")
    if issues and not recently("health", 24):
        msgs.append(("health", "🔧 Bộ thu thập đang thiếu dữ liệu: " + "; ".join(issues) + ". Trang vẫn chạy nhưng kém đầy đủ. Nhắn Claude kèm ảnh chụp trang nếu kéo dài."))

    # 6) bản tin buổi sáng
    if vnow.hour >= 7 and st.get("brief_day") != vday:
        L = [f"☀️ <b>Bản tin tâm lý crypto {vnow:%d/%m}</b>"]
        if ci is not None:
            ref = [v for t, v in rows if t <= time.time() - 23 * 3600]
            L.append(f"Chỉ số tổng hợp: <b>{ci}/100 ({zone(ci)})</b>" + (f", {vn(ci - ref[-1], 0, True)} điểm so với hôm qua" if ref else ""))
        L += summary_lines(latest)
        today = [e for e in cal if e["t"].astimezone(VN).strftime("%Y-%m-%d") == vday and e["t"] > now]
        if today:
            L.append("\nSự kiện cấp 1 hôm nay: " + "; ".join(f"{e['vi']} lúc {e['t'].astimezone(VN):%H:%M}" for e in today))
        msgs.append(("brief", "\n".join(L)))

    if token and chat and not st.get("welcomed"):
        msgs.insert(0, ("welcome", "✅ Đã kết nối cảnh báo của Sentiment Analysis.\nBạn sẽ nhận: bản tin mỗi sáng (khoảng 7–8 giờ), cảnh báo khi tâm lý vào vùng cực đoan, funding bất thường, và nhắc trước các số liệu vĩ mô lớn của Mỹ."))

    status = {"configured": bool(token and chat), "sent": [], "skipped": []}
    for kind, text in msgs:
        key = kind.split(":")[-1]
        if kind.startswith("market"):
            if st["market_count"] >= MAX_MARKET_PER_DAY:
                status["skipped"].append(key)
                continue
        if url and kind != "welcome":
            text += f"\n\n<a href=\"{url}\">Mở trang</a>"
        if not (token and chat):
            status["skipped"].append(key)
            continue
        try:
            send(token, chat, text)
            status["sent"].append(key)
            st["sent"][key] = time.time()
            if kind.startswith("market"):
                st["market_count"] += 1
            if kind == "brief":
                st["brief_day"] = vday
            if kind == "welcome":
                st["welcomed"] = True
            time.sleep(1)
        except Exception as ex:  # noqa: BLE001
            status["error"] = str(ex)[:100]
    if not (token and chat) and any(k == "brief" for k, _ in msgs):
        st["brief_day"] = vday   # chưa cài Telegram thì thôi, không dồn bản tin
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    print("Cảnh báo:", status)
    return status
