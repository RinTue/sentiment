"""Bộ thu thập tâm lý crypto (tầng 1–4), chạy mỗi giờ trên GitHub Actions.

Tầng 1: đọc tin tức (RSS), Reddit, Mastodon, Lemmy, Hacker News.
Tầng 2: lọc trùng, bot, quảng cáo, bài không phải tiếng Anh hoặc không về crypto.
Tầng 3: chấm điểm bằng từ điển thuật ngữ crypto + mô hình Twitter RoBERTa (ONNX).
Tầng 4: tổng hợp chỉ số, lưu data/latest.json và lịch sử data/history.csv.
"""
from __future__ import annotations

import calendar
import csv
import html
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import feedparser
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ITEMS_FILE = os.path.join(DATA, "items.jsonl")
LATEST_FILE = os.path.join(DATA, "latest.json")
HISTORY_FILE = os.path.join(DATA, "history.csv")
KEEP_DAYS = 7
VN = timezone(timedelta(hours=7))
UA = "Mozilla/5.0 (compatible; crypto-sentiment-collector/1.0; personal research)"
NOW = time.time()

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "NewsBTC": "https://www.newsbtc.com/feed/",
    "CryptoPotato": "https://cryptopotato.com/feed/",
}
SUBREDDITS = ["CryptoCurrency", "Bitcoin", "ethereum", "CryptoMarkets", "BitcoinMarkets"]
MASTODON_TAGS = ["bitcoin", "btc", "crypto", "cryptocurrency", "ethereum"]

# ---------------------------------------------------------------- tầng 3a: từ điển
PHRASES = [("to the moon", 2), ("all-time high", 2), ("all time high", 2), ("new high", 1.5), ("record high", 1.6),
           ("buy the dip", 1.2), ("short squeeze", 1), ("long squeeze", -1), ("below support", -1.2), ("above resistance", 1.2),
           ("lost everything", -2.4), ("sell-off", -1.8), ("sell off", -1.8), ("rug pull", -2.2), ("hot cpi", -1),
           ("going up", 1), ("going down", -1)]
WORDS = {
    "bullish": 2, "bull": 1.2, "bulls": 1.2, "moon": 1.8, "mooning": 2, "pump": 1.2, "pumping": 1.5, "pumped": 1.2, "rally": 1.5,
    "rallies": 1.5, "rallying": 1.5, "surge": 1.8, "surges": 1.8, "surging": 1.8, "soar": 1.8, "soars": 1.8, "soaring": 1.8,
    "breakout": 1.5, "ath": 2, "record": 0.8, "buy": 0.6, "buying": 0.9, "bought": 0.6, "accumulate": 1.2, "accumulating": 1.2,
    "accumulation": 1.2, "hodl": 1, "hodling": 1, "wagmi": 1.5, "lfg": 1.5, "gain": 1, "gains": 1.2, "profit": 1.2, "profits": 1.2,
    "green": 0.6, "recover": 1, "recovers": 1.1, "recovery": 1.2, "rebound": 1.4, "rebounds": 1.4, "inflow": 1.5, "inflows": 1.5,
    "adoption": 1.2, "approve": 1.2, "approves": 1.4, "approved": 1.5, "approval": 1.2, "upgrade": 0.8, "strong": 1, "stronger": 1,
    "strength": 1, "optimistic": 1.5, "confident": 1.2, "great": 1.2, "good": 0.7, "love": 1.1, "win": 1, "winning": 1.2,
    "success": 1.2, "successful": 1.2, "opportunity": 0.8, "undervalued": 1, "higher": 0.6, "rise": 1, "rises": 1, "rising": 1,
    "climb": 1, "climbs": 1.2, "jump": 1.2, "jumps": 1.2, "rocket": 1.5, "bullrun": 2, "outperform": 1.2, "outperforms": 1.2,
    "excited": 1.3, "amazing": 1.5, "best": 1, "huge": 0.5, "boost": 1.2, "boosts": 1.2,
    "bearish": -2, "bear": -1.2, "bears": -1.2, "dump": -1.8, "dumps": -1.8, "dumping": -1.8, "dumped": -1.6, "crash": -2,
    "crashes": -2, "crashing": -2, "crashed": -2, "plunge": -2, "plunges": -2, "plunged": -2, "slide": -1.2, "slides": -1.2,
    "sliding": -1.2, "drop": -1.2, "drops": -1.2, "dropped": -1.2, "fall": -1, "falls": -1, "falling": -1.2, "fell": -1.1,
    "sell": -0.6, "selling": -1, "sold": -0.6, "selloff": -1.8, "liquidated": -1.8, "liquidation": -1.5, "liquidations": -1.5,
    "rekt": -2, "ngmi": -1.5, "scam": -2, "scams": -2, "scammer": -2, "scammers": -2, "ponzi": -2.2, "fraud": -2, "rug": -1.8,
    "rugpull": -2.2, "hack": -2, "hacked": -2, "hacks": -2, "exploit": -1.8, "exploited": -1.8, "stolen": -1.8, "outflow": -1.5,
    "outflows": -1.5, "fear": -1.5, "fears": -1.5, "panic": -1.8, "capitulation": -1.8, "red": -0.5, "loss": -1.2,
    "losses": -1.4, "lost": -1.1, "lose": -1.1, "losing": -1.2, "worst": -1.8, "bad": -1, "terrible": -2, "awful": -2,
    "weak": -1, "weaker": -1, "weakness": -1.2, "risky": -0.8, "bubble": -1.4, "collapse": -2, "collapsed": -2, "collapses": -2,
    "bankrupt": -2.2, "bankruptcy": -2.2, "insolvent": -2, "lawsuit": -1.4, "sued": -1.4, "sues": -1.4, "ban": -1.5,
    "bans": -1.5, "banned": -1.5, "crackdown": -1.6, "decline": -1.2, "declines": -1.2, "declining": -1.2, "lower": -0.6,
    "tumble": -1.8, "tumbles": -1.8, "slump": -1.8, "slumps": -1.8, "worry": -1.2, "worries": -1.2, "worried": -1.4,
    "concern": -0.8, "concerns": -0.8, "uncertainty": -1, "overvalued": -1, "useless": -1.8, "waste": -1.4, "garbage": -2,
    "dead": -1.5, "dying": -1.5, "warning": -1, "warns": -1.2, "downturn": -1.5, "recession": -1.4, "inflation": -0.4,
    "delist": -1.6, "delisted": -1.6, "delisting": -1.6, "underperform": -1.2, "underperforms": -1.2, "struggling": -1.3,
    "struggles": -1.3, "hate": -1.5, "stupid": -1.5, "ridiculous": -1.2, "fail": -1.4, "fails": -1.4, "failed": -1.4,
    "failure": -1.6, "failing": -1.4, "greed": -0.3, "fud": -1,
}
EMOJI = [("🚀", 1.5), ("📈", 1.2), ("🟢", 0.8), ("💎", 0.8), ("🐂", 1.2), ("🔥", 0.5), ("📉", -1.2), ("🔴", -0.8),
         ("💀", -1.2), ("😱", -1.2), ("🩸", -1.4), ("🐻", -1.2), ("😭", -1)]
NEG = {"not", "no", "never", "isn't", "wasn't", "aren't", "don't", "doesn't", "didn't", "won't", "can't", "cannot", "hardly",
       "without", "nor", "isnt", "dont", "doesnt", "didnt", "wont", "cant"}


def lex_score(text: str) -> float:
    s = (text or "").lower()[:700]
    total = 0.0
    for p, w in PHRASES:
        if p in s:
            total += w
            s = s.replace(p, " ")
    for e, w in EMOJI:
        if e in s:
            total += w
    toks = [t for t in re.split(r"[^a-z'’-]+", re.sub(r"https?://\S+", " ", s)) if t]
    used = set()
    for i, tk in enumerate(toks):
        key = tk.replace("’", "'")
        w = WORDS.get(key)
        if not w or key in used:
            continue
        used.add(key)
        neg = any(toks[k].replace("’", "'") in NEG for k in range(max(0, i - 3), i))
        total += -0.7 * w if neg else w
    return math.tanh(total / 2.5)


# ---------------------------------------------------------------- tầng 1: thu thập
BOILER = re.compile(r"(the post .{0,200}? appeared first on .{0,80}?\.?$|continue reading.*$|read more.*$|\[…\]|\[\.\.\.\])", re.I)


def strip(markup: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>", " ", markup or "", flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", html.unescape(t)).strip()
    return BOILER.sub("", t).strip()


def get_json(url: str, **kw):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20, **kw)
    r.raise_for_status()
    return r.json()


def fetch_rss() -> tuple[list, dict]:
    items, status = [], {}
    for name, url in RSS_FEEDS.items():
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            n = 0
            for e in feed.entries[:60]:
                ts = e.get("published_parsed") or e.get("updated_parsed")
                t = calendar.timegm(ts) if ts else NOW
                summary = strip(e.get("summary", ""))[:400]
                items.append({"src": "Tin tức", "outlet": name, "id": "rss:" + (e.get("link") or e.get("id") or e.get("title", ""))[:200],
                              "t": t, "text": strip(e.get("title", "")) + ". " + summary, "title": strip(e.get("title", "")),
                              "url": e.get("link"), "author": name, "eng": 0, "lang": "en", "bot": False, "kind": "news"})
                n += 1
            status[name] = n
        except Exception as ex:  # noqa: BLE001
            status[name] = "lỗi: " + str(ex)[:80]
    return items, status


def fetch_reddit() -> tuple[list, dict]:
    """Reddit chặn API JSON từ máy chủ đám mây, nên đọc RSS và nghỉ giữa các lần gọi."""
    items, status = [], {}
    for k, sub in enumerate(SUBREDDITS):
        if k:
            time.sleep(6)
        for attempt in range(2):
            try:
                r = requests.get(f"https://www.reddit.com/r/{sub}/new/.rss?limit=50", headers={"User-Agent": UA}, timeout=20)
                if r.status_code == 429 and attempt == 0:
                    time.sleep(20)
                    continue
                r.raise_for_status()
                feed = feedparser.parse(r.content)
                for e in feed.entries:
                    ts = e.get("updated_parsed") or e.get("published_parsed")
                    items.append({"src": "Reddit", "outlet": "r/" + sub, "id": "rd:" + e.get("id", e.get("link", "")),
                                  "t": calendar.timegm(ts) if ts else NOW, "text": strip(e.get("title", "")) + ". " + strip(e.get("summary", ""))[:600],
                                  "title": strip(e.get("title", "")), "url": e.get("link"), "author": (e.get("author") or ""),
                                  "eng": 0, "lang": "en", "bot": False, "kind": "social"})
                status["r/" + sub] = len(feed.entries)
                break
            except Exception as ex:  # noqa: BLE001
                status["r/" + sub] = "lỗi: " + str(ex)[:80]
                break
    return items, status


def fetch_mastodon() -> tuple[list, dict]:
    items, status = [], {}
    for tag in MASTODON_TAGS:
        try:
            out, max_id = [], None
            for _ in range(3):
                a = get_json(f"https://mastodon.social/api/v1/timelines/tag/{tag}?limit=40" + (f"&max_id={max_id}" if max_id else ""))
                if not a:
                    break
                out += a
                max_id = a[-1]["id"]
            for x in out:
                acct = x.get("account") or {}
                items.append({"src": "Mastodon", "outlet": "#" + tag, "id": "m:" + x["id"], "t": datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")).timestamp(),
                              "text": strip(x.get("content")), "title": None, "url": x.get("url"), "author": acct.get("acct"),
                              "eng": (x.get("favourites_count") or 0) + (x.get("reblogs_count") or 0) + (x.get("replies_count") or 0),
                              "lang": x.get("language"), "bot": bool(acct.get("bot")), "kind": "social"})
            status["#" + tag] = len(out)
        except Exception as ex:  # noqa: BLE001
            status["#" + tag] = "lỗi: " + str(ex)[:80]
    return items, status


def fetch_lemmy_hn() -> tuple[list, dict]:
    items, status = [], {}
    for q in ["bitcoin", "crypto"]:
        try:
            j = get_json(f"https://lemmy.world/api/v3/search?q={q}&type_=All&sort=New&limit=50")
            for p in j.get("posts", []):
                items.append({"src": "Lemmy", "outlet": "lemmy.world", "id": "lp:" + str(p["post"]["id"]), "t": datetime.fromisoformat(p["post"]["published"].replace("Z", "+00:00")).timestamp(),
                              "text": strip(p["post"].get("name", "") + ". " + (p["post"].get("body") or "")), "title": p["post"].get("name"),
                              "url": p["post"].get("ap_id"), "author": (p.get("creator") or {}).get("name"), "eng": (p.get("counts") or {}).get("score", 0),
                              "lang": None, "bot": bool((p.get("creator") or {}).get("bot_account")), "kind": "social"})
            for c in j.get("comments", []):
                items.append({"src": "Lemmy", "outlet": "lemmy.world", "id": "lc:" + str(c["comment"]["id"]), "t": datetime.fromisoformat(c["comment"]["published"].replace("Z", "+00:00")).timestamp(),
                              "text": strip(c["comment"].get("content")), "title": None, "url": c["comment"].get("ap_id"),
                              "author": (c.get("creator") or {}).get("name"), "eng": (c.get("counts") or {}).get("score", 0),
                              "lang": None, "bot": bool((c.get("creator") or {}).get("bot_account")), "kind": "social"})
            status["Lemmy " + q] = len(j.get("posts", [])) + len(j.get("comments", []))
        except Exception as ex:  # noqa: BLE001
            status["Lemmy " + q] = "lỗi: " + str(ex)[:80]
    since = int(NOW) - 2 * 86400
    for q in ["bitcoin", "cryptocurrency", "ethereum"]:
        try:
            j = get_json(f"https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=(story,comment)&numericFilters=created_at_i>{since}&hitsPerPage=300")
            for h in j.get("hits", []):
                items.append({"src": "Hacker News", "outlet": "news.ycombinator.com", "id": "hn:" + h["objectID"], "t": h["created_at_i"],
                              "text": strip((h.get("title") or "") + ". " + (h.get("story_text") or "") if h.get("title") else h.get("comment_text")),
                              "title": h.get("title"), "url": "https://news.ycombinator.com/item?id=" + h["objectID"], "author": h.get("author"),
                              "eng": h.get("points") or 0, "lang": None, "bot": False, "kind": "social"})
            status["HN " + q] = len(j.get("hits", []))
        except Exception as ex:  # noqa: BLE001
            status["HN " + q] = "lỗi: " + str(ex)[:80]
    return items, status


# ---------------------------------------------------------------- tầng 2: lọc
RELEVANT = re.compile(r"\b(bitcoins?|btc|ethereum|ether|eth|solana|xrp|ripple|dogecoin|doge|stablecoins?|altcoins?|memecoins?|cryptocurrenc(y|ies)|crypto (market|markets|price|prices|exchange|exchanges|etf|etfs|asset|assets|investor|investors|trader|traders|trading|bull|bear|crash|winter|rally|regulation|industry|holders?|treasury|adoption)|defi|satoshis?|sats|hodl|coinbase|binance|bybit|kraken|saylor|microstrategy|ibit|halving|blockchain|on-?chain|spot etf|bitcoin etf)\b", re.I)
NOT_CRYPTO = re.compile(r"\b(cryptograph\w*|crypto librar\w*|crypto api|encryption|cipher)\b", re.I)
ADS = re.compile(r"(for sale|in stock|open-box|ready to ship|we accept|paid in btc|discount|giveaway|airdrop|promo code|referral|sign ?up bonus|join (our|my|us)|follow (us|me)|dm me|t\.me/|telegram group|whatsapp|free \d+|claim (your|now)|presale|subscribe)", re.I)
PRICE_BOT = re.compile(r"(price\s*[:：]|market cap\s*[:：]|【.*レポート】|\b24h\b.*%|▲|▼|🔼|🔽)", re.I)


FOREIGN = re.compile(r"\b(und|der|die|das|nicht|mit|auf|von|über|zu|ist|ein|eine|les|des|pour|avec|est|una|los|las|para|con|que|del|por|het|een|niet)\b", re.I)


def looks_foreign(s: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ÿ]+", s)
    return len(words) >= 6 and len(FOREIGN.findall(s)) / len(words) > 0.08


def ascii_ratio(s: str) -> float:
    return sum(1 for ch in s if ord(ch) < 128) / max(1, len(s))


def norm(s: str) -> str:
    s = re.sub(r"https?://\S+|[#@]\w+", "", s.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", s)).strip()[:90]


def clean(items: list) -> tuple[list, dict]:
    reasons = Counter()
    seen, per_author, kept = set(), Counter(), []
    for it in sorted(items, key=lambda x: -x["t"]):
        text = it.get("text") or ""
        core = re.sub(r"https?://\S+|[#@][\w.]+", "", text).strip()
        key = norm(text)
        if not key or key in seen or it["id"] in seen:
            reasons["trùng lặp"] += 1
            continue
        seen.update([key, it["id"]])
        digits = len(re.sub(r"[^0-9]", "", core)) / max(1, len(core))
        if it.get("bot") or PRICE_BOT.search(text) or (it["kind"] == "social" and digits > 0.18):
            reasons["bot / báo giá tự động"] += 1
            continue
        if ADS.search(text):
            reasons["quảng cáo / rao bán"] += 1
            continue
        if (it.get("lang") and it["lang"] != "en") or ascii_ratio(core) < 0.9 or looks_foreign(core):
            reasons["không phải tiếng Anh"] += 1
            continue
        if len(core) < 25:
            reasons["quá ngắn"] += 1
            continue
        if it["kind"] == "social" and (not RELEVANT.search(text) or (NOT_CRYPTO.search(text) and not re.search(r"\b(bitcoin|btc|ethereum|eth|cryptocurrenc)", text, re.I))):
            reasons["không liên quan crypto"] += 1
            continue
        a = it["src"] + ":" + str(it.get("author"))
        per_author[a] += 1
        if it["kind"] == "social" and per_author[a] > 3:
            reasons["cùng tác giả quá nhiều"] += 1
            continue
        kept.append(it)
    return kept, dict(reasons)


# ---------------------------------------------------------------- tầng 3b: mô hình AI (ONNX)
class Model:
    REPO = "Xenova/twitter-roberta-base-sentiment-latest"

    def __init__(self):
        import numpy as np  # noqa: F401
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self.tok = Tokenizer.from_file(hf_hub_download(self.REPO, "tokenizer.json"))
        self.tok.enable_truncation(max_length=256)
        self.tok.enable_padding(pad_id=1, pad_token="<pad>")
        self.sess = ort.InferenceSession(hf_hub_download(self.REPO, "onnx/model_quantized.onnx"), providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        cfg = json.load(open(hf_hub_download(self.REPO, "config.json"), encoding="utf-8"))
        lab = {int(k): v.lower() for k, v in cfg["id2label"].items()}
        self.pos = next(k for k, v in lab.items() if "pos" in v)
        self.neg = next(k for k, v in lab.items() if "neg" in v)

    def score(self, texts: list[str]) -> list[float]:
        import numpy as np
        out = []
        for i in range(0, len(texts), 32):
            batch = [re.sub(r"https?://\S+", "http", re.sub(r"@\w+", "@user", t))[:600] for t in texts[i:i + 32]]
            enc = self.tok.encode_batch(batch)
            feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64), "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
            logits = self.sess.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            e = np.exp(logits - logits.max(axis=1, keepdims=True))
            p = e / e.sum(axis=1, keepdims=True)
            out += (p[:, self.pos] - p[:, self.neg]).tolist()
        return out


def hybrid(ai: float | None, lx: float) -> float:
    if ai is None:
        return lx
    if abs(ai) < 0.35 and abs(lx) >= 0.5:
        return lx * 0.8
    return 0.7 * ai + 0.3 * lx


# ---------------------------------------------------------------- tầng 4: tổng hợp
STOP = set("the a an and or but if then than that this these those there their they them is are was were be been being have has had do does did of to in on for with as at by from about into over after before under between out up down off so not no yes it its it's i you your we our us my me he she his her him what which who whom when where why how all any some more most other such only own same too very can will just should now also like get got one two new would could may might much many even back still well way make made think know see go going said says say really people time year years day days thing things lot good don't im i'm thats that's there's dont doesnt isnt via amp https http www com html week today amid while first post appeared million billion thousand percent according reported report latest since around across continue read october november december january february march april june july august september monday tuesday wednesday thursday friday saturday sunday".split())
GENERIC = set("bitcoin btc crypto cryptocurrency cryptocurrencies ethereum eth coin coins blockchain price prices market markets news token tokens".split())
COINS = [("BTC", r"\b(bitcoin|btc|sats?|satoshi)\b"), ("ETH", r"\b(ethereum|eth|ether)\b"), ("SOL", r"\b(solana|sol)\b"), ("XRP", r"\b(xrp|ripple)\b"),
         ("DOGE", r"\b(dogecoin|doge)\b"), ("BNB", r"\b(bnb)\b"), ("ADA", r"\b(cardano|ada)\b"), ("Stablecoin", r"\b(stablecoins?|usdt|usdc|tether)\b")]
SRC_WEIGHT = {"Tin tức": 1.0, "Reddit": 1.0, "Mastodon": 0.8, "Hacker News": 0.7, "Lemmy": 0.5}


def wmean(arr):
    w = sum(x["w"] for x in arr)
    return sum(x["s"] * x["w"] for x in arr) / w if w else None


def aggregate(kept: list) -> dict:
    for x in kept:
        x["w"] = (1 + math.log1p(max(0, x.get("eng") or 0))) * SRC_WEIGHT.get(x["src"], 1)
        x["label"] = "pos" if x["s"] > 0.15 else "neg" if x["s"] < -0.15 else "neu"
    last24 = [x for x in kept if NOW - x["t"] <= 86400]
    prev = [x for x in kept if 86400 < NOW - x["t"] <= 3 * 86400]
    base = last24 if len(last24) >= 40 else [x for x in kept if NOW - x["t"] <= 2 * 86400]

    def block(arr):
        if not arr:
            return None
        sc = wmean(arr)
        n = len(arr)
        return {"score": round(sc, 4), "index": round((sc + 1) * 50), "n": n,
                "pos": round(100 * sum(x["label"] == "pos" for x in arr) / n, 1),
                "neg": round(100 * sum(x["label"] == "neg" for x in arr) / n, 1),
                "neu": round(100 * sum(x["label"] == "neu" for x in arr) / n, 1)}

    days = defaultdict(list)
    for x in kept:
        days[datetime.fromtimestamp(x["t"], VN).strftime("%Y-%m-%d")].append(x)
    daily = [{"date": d, "n": len(a), "score": round(wmean(a), 4)} for d, a in sorted(days.items()) if len(a) >= 5][-7:]

    wc = Counter()
    for x in base:
        words = set(w.strip("-") for w in re.split(r"[^a-z0-9$-]+", re.sub(r"https?://\S+", " ", x["text"].lower())))
        wc.update(w for w in words if len(w) >= 4 and w not in STOP and w not in GENERIC and not w.isdigit())
    coins = sorted(((c, sum(1 for x in base if re.search(p, x["text"], re.I))) for c, p in COINS), key=lambda t: -t[1])

    def post(x):
        return {"src": x["src"], "outlet": x.get("outlet"), "t": int(x["t"] * 1000), "s": round(x["s"], 3),
                "text": (x.get("title") or x["text"])[:240], "url": x.get("url")}

    ranked = sorted([x for x in base if len(x["text"]) >= 40], key=lambda x: x["s"] * x["w"], reverse=True)
    by_src = defaultdict(list)
    for x in base:
        by_src[x["src"]].append(x)

    return {
        "all": block(base), "news": block([x for x in base if x["kind"] == "news"]), "social": block([x for x in base if x["kind"] == "social"]),
        "trend": round(wmean(last24) - wmean(prev), 4) if len(last24) >= 20 and len(prev) >= 20 else None,
        "window_hours": 24 if base is last24 else 48,
        "daily": daily,
        "sources": [{"src": k, "n": len(a), "score": round(wmean(a), 4)} for k, a in sorted(by_src.items(), key=lambda t: -len(t[1]))],
        "keywords": [[w, n] for w, n in wc.most_common(14) if n >= 3],
        "coins": [[c, n] for c, n in coins if n],
        "top_pos": [post(x) for x in ranked if x["label"] == "pos"][:5],
        "top_neg": [post(x) for x in reversed(ranked) if x["label"] == "neg"][:5],
    }


# ---------------------------------------------------------------- chạy
def load_items() -> dict:
    out = {}
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                    out[x["id"]] = x
                except json.JSONDecodeError:
                    continue
    return out


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    started = time.time()
    raw, status = [], {}
    for fn in (fetch_rss, fetch_reddit, fetch_mastodon, fetch_lemmy_hn):
        a, st = fn()
        raw += a
        status.update(st)
    print(f"Thu thập: {len(raw)} bài thô")

    kept, reasons = clean(raw)
    store = load_items()
    new = [x for x in kept if x["id"] not in store]
    print(f"Lọc: giữ {len(kept)}, mới {len(new)}")

    model_status = "từ điển"
    ai_scores = [None] * len(new)
    if new:
        try:
            m = Model()
            ai_scores = m.score([x["text"] for x in new])
            model_status = "AI + từ điển"
        except Exception as ex:  # noqa: BLE001
            model_status = "từ điển (AI lỗi: " + str(ex)[:120] + ")"
            print("Không chạy được mô hình:", ex, file=sys.stderr)
    for x, a in zip(new, ai_scores):
        lx = lex_score(x["text"])
        x["lex"] = round(lx, 4)
        x["ai"] = None if a is None else round(a, 4)
        x["s"] = round(hybrid(a, lx), 4)
        store[x["id"]] = {k: x.get(k) for k in ("id", "src", "outlet", "t", "text", "title", "url", "author", "eng", "kind", "lex", "ai", "s")}

    cutoff = NOW - KEEP_DAYS * 86400
    store = {k: v for k, v in store.items() if v["t"] >= cutoff}
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        for v in sorted(store.values(), key=lambda v: v["t"]):
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    with_ai = sum(1 for v in store.values() if v.get("ai") is not None)
    if store and with_ai:
        model_status = f"AI + từ điển ({round(100 * with_ai / len(store))}% số bài có điểm AI)" + ("" if not model_status.startswith("từ điển (AI lỗi") else "; lần chạy này AI lỗi")
    agg = aggregate(list(store.values()))
    stamp = datetime.now(timezone.utc)
    latest = {"generated_at": stamp.isoformat(timespec="seconds"), "scoring": model_status, "raw_count": len(raw), "kept_count": len(kept),
              "new_count": len(new), "stored_7d": len(store), "filter_reasons": reasons, "source_status": status,
              "runtime_sec": round(time.time() - started, 1), **agg}
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)

    new_file = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time_utc", "index_all", "score_all", "n_all", "index_news", "index_social", "pos_pct", "neg_pct"])
        a, nw, so = agg["all"] or {}, agg["news"] or {}, agg["social"] or {}
        w.writerow([stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), a.get("index", ""), a.get("score", ""), a.get("n", ""), nw.get("index", ""), so.get("index", ""), a.get("pos", ""), a.get("neg", "")])
    print(json.dumps({k: latest[k] for k in ("scoring", "kept_count", "new_count", "stored_7d")}, ensure_ascii=False), agg["all"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
