"""Thử nghiệm: chấm song song bằng CryptoBERT (huấn luyện trên bài đăng crypto) để so với mô hình đang dùng.

Không thay đổi điểm chính. Ghi điểm "cb" vào từng bài, chấm bộ câu thử có nhãn, lưu so sánh vào latest.json["model_compare"]
và data/model_history.csv. Chạy ở một bước riêng của workflow; lỗi ở đây không ảnh hưởng phần còn lại.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402

REPO = "ElKulako/cryptobert"
HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_FILE = os.path.join(HERE, "eval_set.json")
COMPARE_FILE = os.path.join(collect.DATA, "model_compare.json")
MODEL_HIST = os.path.join(collect.DATA, "model_history.csv")
MAX_PER_RUN = 600


class CryptoBert:
    def __init__(self):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(max(1, os.cpu_count() or 2))
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(REPO, use_fast=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(REPO).eval()
        lab = {v.lower(): int(k) for k, v in self.model.config.id2label.items()}
        self.pos, self.neg = lab.get("bullish", 2), lab.get("bearish", 0)

    def score(self, texts: list[str]) -> list[float]:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), 32):
                batch = [re.sub(r"https?://\S+", "HTTPURL", re.sub(r"@\w+", "@USER", t))[:500] for t in texts[i:i + 32]]
                enc = self.tok(batch, padding=True, truncation=True, max_length=128, return_tensors="pt")
                p = self.torch.softmax(self.model(**enc).logits, dim=-1)
                out += (p[:, self.pos] - p[:, self.neg]).tolist()
        return out


def label(s: float) -> str:
    return "pos" if s > 0.15 else "neg" if s < -0.15 else "neu"


def evaluate(cb: CryptoBert) -> dict:
    """Chấm bộ câu thử bằng: từ điển, mô hình hiện tại (AI + từ điển), CryptoBERT, CryptoBERT + từ điển."""
    data = json.load(open(EVAL_FILE, encoding="utf-8"))["items"]
    texts, gold = [t for _, t in data], [g for g, _ in data]
    lex = [collect.lex_score(t) for t in texts]
    try:
        cur_ai = collect.Model().score(texts)
    except Exception as ex:  # noqa: BLE001
        print("Không chạy được mô hình hiện tại:", ex, file=sys.stderr)
        cur_ai = [None] * len(texts)
    cbs = cb.score(texts)
    systems = {
        "Từ điển": lex,
        "Đang dùng: Twitter RoBERTa + từ điển": [collect.hybrid(a, l) for a, l in zip(cur_ai, lex)],
        "CryptoBERT": cbs,
        "CryptoBERT + từ điển": [collect.hybrid(c, l) for c, l in zip(cbs, lex)],
    }
    res = {}
    for name, sc in systems.items():
        lab = [label(s) for s in sc]
        acc = sum(1 for a, b in zip(lab, gold) if a == b) / len(gold)
        pn = [(a, b) for a, b in zip(lab, gold) if b != "neu"]
        wrong_dir = sum(1 for a, b in pn if a != "neu" and a != b)
        res[name] = {"acc": round(100 * acc), "dir_ok": round(100 * sum(1 for a, b in pn if a == b) / len(pn)),
                     "wrong_dir": wrong_dir, "n": len(gold),
                     "by": {g: round(100 * sum(1 for a, b in zip(lab, gold) if b == g and a == g) / gold.count(g)) for g in ("pos", "neg", "neu")}}
    return res


def main() -> int:
    t0 = time.time()
    cb = CryptoBert()
    store = collect.load_items()
    todo = sorted([x for x in store.values() if x.get("cb") is None], key=lambda x: -x["t"])[:MAX_PER_RUN]
    if todo:
        for x, s in zip(todo, cb.score([x["text"] for x in todo])):
            x["cb"] = round(s, 4)
        with open(collect.ITEMS_FILE, "w", encoding="utf-8") as f:
            for v in sorted(store.values(), key=lambda v: v["t"]):
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
    print(f"CryptoBERT chấm {len(todo)} bài trong {time.time() - t0:.0f} giây")

    # Bộ câu thử: chỉ chấm lại khi bộ câu hoặc mô hình đổi.
    try:
        old = json.load(open(COMPARE_FILE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        old = {}
    h = hashlib.sha1((open(EVAL_FILE, encoding="utf-8").read() + REPO + collect.Model.REPO).encode()).hexdigest()[:12]
    ev = old.get("eval") if old.get("eval_hash") == h else None
    if ev is None:
        ev = evaluate(cb)

    # So sánh trên dữ liệu thật 24 giờ qua (cùng trọng số nguồn với chỉ số chính).
    now = time.time()
    both = [x for x in store.values() if now - x["t"] <= 86400 and x.get("cb") is not None and x.get("s") is not None]
    live = {}
    if both:
        for x in both:
            x["w"] = collect.SRC_WEIGHT.get(x["src"], 1)
            x["s_cb"] = collect.hybrid(x["cb"], x.get("lex") or collect.lex_score(x["text"]))
        W = sum(x["w"] for x in both)
        m_cur = sum(x["s"] * x["w"] for x in both) / W
        m_cb = sum(x["s_cb"] * x["w"] for x in both) / W
        agree = sum(1 for x in both if label(x["s"]) == label(x["s_cb"])) / len(both)
        opposite = [x for x in both if {label(x["s"]), label(x["s_cb"])} == {"pos", "neg"}]
        live = {"n": len(both), "index_current": round((m_cur + 1) * 50), "index_cryptobert": round((m_cb + 1) * 50), "agree": round(100 * agree),
                "examples": [{"src": x["src"], "text": x["text"][:220], "current": round(x["s"], 2), "cryptobert": round(x["s_cb"], 2)}
                             for x in sorted(opposite, key=lambda x: -abs(x["s"] - x["s_cb"]))[:4]]}
        new = not os.path.exists(MODEL_HIST)
        with open(MODEL_HIST, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time_utc", "n", "index_current", "index_cryptobert", "agree_pct"])
            w.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), live["n"], live["index_current"], live["index_cryptobert"], live["agree"]])

    out = {"eval_hash": h, "eval": ev, "live": live, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "runtime_sec": round(time.time() - t0, 1)}
    with open(COMPARE_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    try:
        latest = json.load(open(collect.LATEST_FILE, encoding="utf-8"))
        latest["model_compare"] = out
        with open(collect.LATEST_FILE, "w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, indent=1)
    except (OSError, json.JSONDecodeError) as ex:
        print("Không ghi được latest.json:", ex, file=sys.stderr)
    print(json.dumps({k: v for k, v in out.items() if k != "live"}, ensure_ascii=False)[:600], live.get("index_current"), live.get("index_cryptobert"), live.get("agree"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
