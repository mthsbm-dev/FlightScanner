#!/usr/bin/env python3
"""Generate a readable landlord mail digest and send it via email.

Behavior:
- Runs `mbsync aner_mathias` to refresh local Maildir.
- Uses `scripts/check_landlord_mail.py` for incremental collection.
- Builds a structured digest:
  - Header with counts (by folder + by category)
  - Optional "WICHTIG" section (heuristic)
  - Details listed only for high-signal categories (Vermieter, Börse/Aktien, Athena)
    grouped by thread ("(+n ähnlich)")
  - Low-signal categories (Werbung/Newsletter, Sonstiges) are summarized only
    (top senders + top threads)

Config: workspace/config/landlord_digest.json
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, parseaddr
from email import policy
from email.parser import BytesParser
import tempfile
from pathlib import Path
import smtplib
import hashlib

WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG_PATH = WORKSPACE / "config" / "landlord_digest.json"
RULES_PATH = WORKSPACE / "config" / "digest_rules.json"
CHECK_SCRIPT = WORKSPACE / "scripts" / "check_landlord_mail.py"
LOCAL_LLM_SH = WORKSPACE / "scripts" / "local_llm.sh"
JUDGE_HELPER = WORKSPACE / "scripts" / "anthropic_judge.py"
JUDGE_CACHE_PATH = WORKSPACE / "memory" / "digest-judge-cache.json"

HIGH_SIGNAL_CATS = ("Vermieter", "Börse/Aktien", "Athena")
LOW_SIGNAL_CATS = ("Werbung/Newsletter", "Sonstiges")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_rules() -> dict:
    if RULES_PATH.exists():
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return {}


def run_mbsync(timeout_s: int = 15) -> None:
    """Sync local Maildir. Hard timeout so digests can't hang silently."""
    try:
        proc = subprocess.run(
            ["mbsync", "aner_mathias"],
            cwd=str(Path.home()),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"mbsync timed out after {timeout_s}s")

    if proc.returncode != 0:
        raise RuntimeError(f"mbsync failed ({proc.returncode}): {proc.stderr[:500] or proc.stdout[:500]}")


def run_check() -> dict:
    proc = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT)],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"check_landlord_mail.py failed ({proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout)


def _norm(s: str) -> str:
    return (s or "").strip()


def _short_from(from_header: str) -> str:
    name, addr = parseaddr(from_header or "")
    addr = (addr or "").lower()
    name = _norm(name)
    if name and addr:
        return f"{name} <{addr}>"
    return name or addr or "(unknown)"


def _addr(from_header: str) -> str:
    return (parseaddr(from_header or "")[1] or "").lower()


def clean_snip(text: str) -> str:
    """Clean up common newsletter/HTML junk so previews are readable."""
    t = (text or "").strip()

    # remove zero-width &nbsp-ish artifacts often seen in newsletters
    t = t.replace("\u200c", " ").replace("\u200d", " ")
    t = t.replace("\ufeff", " ")
    t = t.replace("&zwnj;", " ")

    # collapse whitespace
    t = re.sub(r"\s+", " ", t)

    # strip boilerplate phrases
    boiler = [
        r"Für Webansicht hier klicken.*$",
        r"Nachricht im Browser ansehen.*$",
        r"Wenn Sie diese E-Mail.*$",
        r"Abbestellen:.*$",
        r"unsubscribe.*$",
        r"impressum.*$",
        r"datenschutz.*$",
    ]
    for pat in boiler:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)

    # remove long URL blobs
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\(\s*\)", "", t)

    # final cleanup
    t = re.sub(r"\s+", " ", t).strip(" -|\n\t")
    return t


def _clip(text: str, n: int = 240) -> str:
    t = clean_snip(text)
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def thread_key(subject: str) -> str:
    """Normalize subject so follow-ups can be grouped (heuristic)."""
    s = _norm(subject)
    s = re.sub(r"^(re|aw|fw|wg|fwd)\s*:\s*", "", s, flags=re.IGNORECASE)
    # appointment-like prefixes
    s = re.sub(
        r"^(erster|zweiter|dritter|vierter|fünfter|sechster|siebter|achter|neunter|zehnter)\s+termin\s*[-:]\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"^termin\s*[-:]\s*", "", s, flags=re.IGNORECASE)
    return s.strip().lower()


def get_domain(addr: str) -> str:
    a = (addr or "").lower().strip()
    if "@" in a:
        return a.split("@", 1)[1]
    return ""


def _contains_any(hay: str, needles: list[str]) -> bool:
    h = (hay or "").lower()
    return any(n.lower() in h for n in needles)


def rule_classify(m: dict, rules: dict) -> tuple[str, float, str, str, str]:
    """Return (category, confidence, list_mode, importance, reason).

    list_mode: detail|summary|suppress
    importance: none|low|high

    This stage is cheap/deterministic and should be high precision.
    """
    subj = _norm(m.get("subject", ""))
    subj_l = subj.lower()
    from_addr = _addr(m.get("from", ""))
    domain = get_domain(from_addr)

    cat, conf, list_mode, importance, reason = "Sonstiges", 0.3, "summary", "none", "default"

    # hard suppression
    matched = False
    for pat in rules.get("suppress_subject_regex", []) if rules else []:
        try:
            if re.search(pat, subj):
                cat = "Athena" if "landlord digest" in subj_l else "Werbung/Newsletter"
                conf, list_mode, importance, reason = 1.0, "suppress", "none", f"suppressed:{pat}"
                matched = True
                break
        except re.error:
            continue

    # forced sender overrides
    if not matched:
        force = (rules.get("sender_force_category", {}) if rules else {}).get(from_addr)
        if force:
            matched = True
            if force == "Börse/Aktien":
                lm = "detail" if any(t in subj_l for t in rules.get("boerse_detail_triggers", [])) else "summary"
                cat, conf, list_mode, importance, reason = force, 0.95, lm, "low", "forced-sender"
            elif force == "Athena":
                cat, conf, list_mode, importance, reason = "Athena", 0.99, "detail", "low", "forced-sender"
            elif force == "Werbung/Newsletter":
                cat, conf, list_mode, importance, reason = "Werbung/Newsletter", 0.95, "summary", "none", "forced-sender"
            else:
                cat, conf, list_mode, importance, reason = force, 0.95, "detail", "low", "forced-sender"

    # domain allowlists
    if not matched:
        dom_allow = rules.get("sender_domain_allow", {}) if rules else {}
        for dcat, doms in dom_allow.items():
            if domain and any(domain.endswith(d) for d in doms):
                matched = True
                if dcat == "Börse/Aktien":
                    lm = "detail" if any(t in subj_l for t in rules.get("boerse_detail_triggers", [])) else "summary"
                    cat, conf, list_mode, importance, reason = "Börse/Aktien", 0.9, lm, "low", "domain-allow"
                elif dcat == "Werbung/Newsletter":
                    cat, conf, list_mode, importance, reason = "Werbung/Newsletter", 0.9, "summary", "none", "domain-allow"
                elif dcat == "Vermieter":
                    imp = "high" if _contains_any(subj_l, ["defekt", "schaden", "mahnung", "frist", "zahlung"]) else "low"
                    cat, conf, list_mode, importance, reason = "Vermieter", 0.9, "detail", imp, "domain-allow"
                break

    # hard subject patterns
    if not matched:
        spat = rules.get("subject_hard_patterns", {}) if rules else {}
        for scat, keys in spat.items():
            if _contains_any(subj, keys):
                matched = True
                if scat == "Börse/Aktien":
                    lm = "detail" if any(t in subj_l for t in rules.get("boerse_detail_triggers", [])) else "summary"
                    cat, conf, list_mode, importance, reason = "Börse/Aktien", 0.8, lm, "low", "subject-pattern"
                elif scat == "Vermieter":
                    imp = "high" if _contains_any(subj_l, ["defekt", "schaden", "mahnung", "frist", "zahlung"]) else "low"
                    cat, conf, list_mode, importance, reason = "Vermieter", 0.8, "detail", imp, "subject-pattern"
                break

    # attachments upgrade importance (none→low); higher upgrades left to the judge
    if m.get("has_real_attachments") and importance == "none":
        importance = "low"

    return cat, conf, list_mode, importance, reason


def is_suppressed(m: dict) -> bool:
    """Suppressed items are never listed individually, only summarized."""
    subj = _norm(m.get("subject") or "")
    subj_l = subj.lower()

    # never list digest mails (avoid recursion/noise)
    if "landlord digest" in subj_l:
        return True

    # explicit low-signal spam words
    if "gratis" in subj_l:
        return True

    # repetitive appointment reminders (typically low-signal)
    if re.search(r"(?i)^(erster|zweiter|dritter|vierter|fünfter)\s+termin\b", subj):
        return True

    return False


def msg_fingerprint(m: dict) -> str:
    """Stable-ish key for caching judge results."""
    blob = "\n".join(
        [
            _norm(m.get("folder", "")),
            _norm(m.get("from", "")),
            _norm(m.get("to", "")),
            _norm(m.get("subject", "")),
            _norm(m.get("date_header", "")),
        ]
    ).encode("utf-8", errors="ignore")
    return hashlib.sha256(blob).hexdigest()[:24]


def load_judge_cache() -> dict:
    if JUDGE_CACHE_PATH.exists():
        try:
            return json.loads(JUDGE_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_judge_cache(cache: dict) -> None:
    JUDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    JUDGE_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def phi_classify(todo: list[dict], max_items: int = 10, timeout_s: int = 8) -> list[dict]:
    """Local cheap classification via phi3 (Ollama) using scripts/local_llm.sh.

    Returns list of objects: {id, category, list_mode, importance, confidence}

    NOTE: We cap items and apply a timeout to avoid stalling the whole digest.
    """
    if not todo:
        return []

    todo = todo[:max_items]

    prompt = (
        "Classify emails into categories for a landlord+investing digest.\n"
        "Return ONLY JSON array. Each element: {id, category, list_mode, importance, confidence}.\n"
        "Allowed category: Vermieter|Börse/Aktien|Athena|Werbung/Newsletter|Sonstiges.\n"
        "Allowed list_mode: detail|summary|suppress.\n"
        "importance: none|low|high.\n"
        "Börse/Aktien should be summary unless it contains alerts/dividends/earnings.\n"
        "confidence is 0..1. Be conservative.\n\n"
        "Items:\n"
        + json.dumps(todo, ensure_ascii=False)
    )

    try:
        proc = subprocess.run(
            [str(LOCAL_LLM_SH)],
            cwd=str(WORKSPACE),
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return []

    if proc.returncode != 0:
        return []

    out = proc.stdout.strip()
    # try to extract json substring
    m = re.search(r"[\[{]", out)
    if m:
        out = out[m.start():]
    last = max(out.rfind('}'), out.rfind(']'))
    if last != -1:
        out = out[: last + 1]

    try:
        arr = json.loads(out)
    except Exception:
        return []

    if not isinstance(arr, list):
        return []
    return arr


def judge_classify(messages: list[dict], rules: dict, max_judge: int = 6) -> dict[str, dict]:
    """Hybrid classifier:

    - Stage 1: deterministic rules with confidence
    - Stage 2: phi3 for uncertain items
    - Stage 3: Claude Opus 4.6 judge only for remaining uncertain (capped)

    Returns dict: fingerprint -> {category, list_mode, importance, source, confidence}
    Also stores rules in __rules__ for downstream usage.
    """
    cache = load_judge_cache()
    cache["__rules__"] = rules

    # Build candidates (new or potentially uncertain only)
    candidates = []
    for m in messages:
        fp = msg_fingerprint(m)
        if fp in cache:
            continue
        cat, conf, list_mode, importance, reason = rule_classify(m, rules)
        candidates.append(
            {
                "fp": fp,
                "folder": m.get("folder", ""),
                "from": _addr(m.get("from", "")) or _norm(m.get("from", "")),
                "subject": _norm(m.get("subject", "")),
                "body_snip": _clip(m.get("body", ""), 220),
                "has_real_attachments": bool(m.get("has_real_attachments")),
                "attachments": m.get("attachments", []),
                "rule": {"category": cat, "confidence": conf, "list_mode": list_mode, "importance": importance, "reason": reason},
            }
        )

    # Accept high-confidence rules immediately
    for c in candidates:
        r = c["rule"]
        if r["confidence"] >= 0.9:
            cache[c["fp"]] = {
                "category": r["category"],
                "list_mode": r["list_mode"],
                "importance": r["importance"],
                "source": "rules",
                "confidence": r["confidence"],
            }

    # Phi stage for remaining uncertain
    phi_todo = []
    for c in candidates:
        if c["fp"] in cache:
            continue
        if c["rule"]["confidence"] >= 0.7:
            # decent rule guess; don't spend phi unless conflicting keywords
            cache[c["fp"]] = {
                "category": c["rule"]["category"],
                "list_mode": c["rule"]["list_mode"],
                "importance": c["rule"]["importance"],
                "source": "rules",
                "confidence": c["rule"]["confidence"],
            }
            continue
        phi_todo.append({"id": c["fp"], "from": c["from"], "subject": c["subject"], "body_snip": c["body_snip"]})

    phi_res = {x.get("id"): x for x in phi_classify(phi_todo)}
    for c in candidates:
        if c["fp"] in cache:
            continue
        pr = phi_res.get(c["fp"])
        if not pr:
            continue
        conf = float(pr.get("confidence", 0) or 0)
        if conf >= 0.75:
            cache[c["fp"]] = {
                "category": pr.get("category"),
                "list_mode": pr.get("list_mode", "summary"),
                "importance": pr.get("importance", "low"),
                "source": "phi",
                "confidence": conf,
            }

    # Judge stage for what remains (cap)
    todo = []
    for c in candidates:
        if c["fp"] in cache:
            continue
        todo.append({
            "id": c["fp"],
            "folder": c["folder"],
            "from": c["from"],
            "subject": c["subject"],
            "body_snip": c["body_snip"],
            "has_real_attachments": bool(c.get("has_real_attachments")),
            "attachments": c.get("attachments", [])
        })

    if todo:
        todo = todo[:max_judge]
        prompt = (
            "You are a strict email triage judge for a landlord+investing digest.\n"
            "Classify each item into exactly one category: Vermieter, Börse/Aktien, Athena, Werbung/Newsletter, Sonstiges.\n"
            "Also decide list_mode: detail|summary|suppress. importance: none|low|high.\n"
            "Börse/Aktien should be summary unless it contains alerts/dividends/earnings.\n"
            "Return ONLY JSON array. Each element MUST be {id, category, list_mode, importance}.\n\n"
            "Items:\n"
            + json.dumps(todo, ensure_ascii=False)
        )

        try:
            proc = subprocess.run(
                [sys.executable, str(JUDGE_HELPER)],
                cwd=str(WORKSPACE),
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            proc = None

        if proc and proc.returncode == 0:
            out = proc.stdout.strip()
            try:
                arr = json.loads(out)
                for obj in arr:
                    cid = obj.get("id")
                    if not cid:
                        continue
                    cache[cid] = {
                        "category": obj.get("category"),
                        "list_mode": obj.get("list_mode", "summary"),
                        "importance": obj.get("importance", "low"),
                        "source": "claude",
                        "confidence": 0.95,
                    }
            except Exception:
                pass

    save_judge_cache(cache)
    return cache


@dataclass
class DigestEmail:
    folder: str
    category: str
    from_short: str
    from_addr: str
    subject: str
    timestamp: str
    body_snip: str
    suppressed: bool
    thread: str
    path: str | None = None
    attachments: list[dict] | None = None
    has_real_attachments: bool = False


def to_digest_email(m: dict, judge_cache: dict[str, dict] | None = None) -> DigestEmail:
    folder = m.get("folder", "?")
    subject = _norm(m.get("subject") or "(ohne Betreff)")
    ts = _norm(m.get("timestamp") or "").replace("T", " ").replace("+00:00", "Z")
    frm = _short_from(m.get("from") or "")
    addr = _addr(m.get("from") or "")
    body = _clip(m.get("body") or "", 420)
    path = m.get("path")
    attachments = m.get("attachments") or []
    has_real_attachments = bool(m.get("has_real_attachments"))

    # default deterministic categorization (rules)
    rules = judge_cache.get("__rules__") if isinstance(judge_cache, dict) else None
    cat, conf, list_mode, importance, reason = rule_classify(m, rules or {})
    sup = is_suppressed(m) or (list_mode in ("summary", "suppress"))

    # Phi / Claude judge override
    if judge_cache is not None:
        fp = msg_fingerprint(m)
        j = judge_cache.get(fp)
        if j:
            jcat = j.get("category")
            if jcat in ("Vermieter", "Börse/Aktien", "Athena", "Werbung/Newsletter", "Sonstiges"):
                cat = jcat
            mode = j.get("list_mode")
            if mode in ("suppress", "summary"):
                sup = True
            elif mode == "detail":
                sup = is_suppressed(m)

    return DigestEmail(
        folder=folder,
        category=cat,
        from_short=frm,
        from_addr=addr,
        subject=subject,
        timestamp=ts,
        body_snip=body,
        suppressed=sup,
        thread=thread_key(subject),
        path=str(path) if path else None,
        attachments=attachments,
        has_real_attachments=has_real_attachments,
    )


def extract_highlights(items: list[DigestEmail], max_items: int = 8) -> list[str]:
    """Heuristic highlights: deadlines, payments, amounts, dates."""
    highlights: list[str] = []
    money_re = re.compile(r"(\d+[\d\.,]*\s?(€|eur))", re.IGNORECASE)
    date_re = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})\b")

    for e in items:
        if e.category not in ("Vermieter", "Börse/Aktien"):
            continue
        blob = (e.subject + "\n" + e.body_snip).lower()
        if not any(k in blob for k in ["frist", "dring", "heute", "morgen", "termin", "zahlung", "überweisen", "kündig", "mahnung", "reparatur", "schaden", "handwerker", "wartung", "dividende", "earnings", "kursalarm"]):
            continue
        money = money_re.search(blob)
        date = date_re.search(blob)
        extra = ""
        if money:
            extra += f" · Betrag: {money.group(1)}"
        if date:
            extra += f" · Datum: {date.group(1)}"
        highlights.append(f"- [{e.folder}] {e.subject}{extra}")
        if len(highlights) >= max_items:
            break

    return highlights


def attachment_summary(e: DigestEmail) -> str:
    atts = e.attachments or []
    if not atts:
        return ""
    parts = []
    for a in atts[:5]:
        fn = (a.get('filename') or '').strip() or '(no name)'
        ct = (a.get('content_type') or '').strip()
        sz = a.get('size_bytes')
        szs = f"{int(sz/1024)}KB" if isinstance(sz, int) else "?"
        parts.append(f"{fn} ({ct}, {szs})")
    more = f" +{len(atts)-5} more" if len(atts) > 5 else ""
    return "; ".join(parts) + more


def pdf_preview_from_maildir(e: DigestEmail, max_bytes: int = 2_500_000) -> str:
    """Extract a short text preview from the FIRST PDF attachment (page 1 only)."""
    if not e.path or not e.attachments:
        return ""

    # pick first pdf-ish attachment
    pdf_name = None
    for a in e.attachments:
        fn = (a.get('filename') or '').lower()
        ct = (a.get('content_type') or '').lower()
        if fn.endswith('.pdf') or ct == 'application/pdf':
            pdf_name = a.get('filename')
            break
    if not pdf_name:
        return ""

    try:
        raw = Path(e.path).read_bytes()
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:
        return ""

    pdf_bytes = None
    for part in msg.walk() if msg.is_multipart() else []:
        if part.is_multipart():
            continue
        fn = part.get_filename() or ''
        if isinstance(fn, bytes):
            fn = fn.decode('utf-8', errors='replace')
        if fn != pdf_name:
            continue
        try:
            pdf_bytes = part.get_payload(decode=True)
        except Exception:
            pdf_bytes = None
        break

    if not pdf_bytes or len(pdf_bytes) > max_bytes:
        return ""

    try:
        with tempfile.TemporaryDirectory() as td:
            in_path = Path(td) / 'att.pdf'
            out_path = Path(td) / 'out.txt'
            in_path.write_bytes(pdf_bytes)
            # page 1 only
            subprocess.run(['pdftotext', '-f', '1', '-l', '1', str(in_path), str(out_path)], check=False, timeout=10)
            if out_path.exists():
                text = out_path.read_text(errors='ignore')
                text = clean_snip(text)
                return _clip(text, 280)
    except Exception:
        return ""

    return ""


def render_text(now: datetime, all_items: list[DigestEmail], imap_warnings: list[dict] | None = None) -> str:
    folder_counts = Counter([e.folder for e in all_items])
    cat_counts = Counter([e.category for e in all_items])
    imap_warnings = imap_warnings or []

    # Important section considers non-suppressed high-signal emails
    important_pool = [e for e in all_items if (e.category in ("Vermieter", "Börse/Aktien") and not e.suppressed)]
    highlights = extract_highlights(important_pool)

    lines: list[str] = []
    lines.append("LANDLORD DIGEST")
    lines.append("=" * 72)
    lines.append(f"Erstellt (UTC): {now.strftime('%Y-%m-%d %H:%M')}Z")
    lines.append(f"Neue Mails:     {len(all_items)}")
    lines.append(
        "Ordner:        " + ", ".join([f"{k}: {v}" for k, v in folder_counts.items()])
    )
    lines.append(
        "Themen:        " + ", ".join([f"{k}: {v}" for k, v in cat_counts.items()])
    )
    lines.append("")

    if imap_warnings:
        lines.append("WARNUNGEN")
        lines.append("-" * 72)
        for w in imap_warnings[:5]:
            lines.append(f"- {w.get('type')}: {w.get('hint','')}")
            if w.get('unseen'):
                lines.append(f"  UNSEEN: {w.get('unseen')}")
        lines.append("")

    if highlights:
        lines.append("WICHTIG (heuristisch)")
        lines.append("-" * 72)
        lines.extend(highlights)
        lines.append("")

    # Group: category -> threads
    by_cat: dict[str, list[DigestEmail]] = defaultdict(list)
    for e in all_items:
        by_cat[e.category].append(e)

    # High-signal details
    for cat in HIGH_SIGNAL_CATS:
        items = [e for e in by_cat.get(cat, []) if not e.suppressed]
        if not items:
            continue
        lines.append(cat.upper())
        lines.append("-" * 72)
        by_thread: dict[str, list[DigestEmail]] = defaultdict(list)
        for e in items:
            by_thread[e.thread].append(e)

        threads_sorted = sorted(
            by_thread.items(),
            key=lambda kv: max((x.timestamp for x in kv[1]), default=""),
            reverse=True,
        )
        for idx, (_t, group) in enumerate(threads_sorted[:15], start=1):
            group_sorted = sorted(group, key=lambda x: x.timestamp, reverse=True)
            e0 = group_sorted[0]
            suffix = f" (+{len(group_sorted)-1} ähnlich)" if len(group_sorted) > 1 else ""
            lines.append(f"{idx:02d}. [{e0.folder}] {e0.subject}{suffix}")
            lines.append(f"    Von:  {e0.from_short}")
            lines.append(f"    Zeit: {e0.timestamp}")

            if e0.has_real_attachments:
                lines.append(f"    Anhang: {attachment_summary(e0)}")
                pdf_prev = pdf_preview_from_maildir(e0)
                if pdf_prev:
                    lines.append(f"    PDF-Preview: {pdf_prev}")

            # Only show content snippet when it adds value.
            show_body = (
                (e0.category in ("Vermieter", "Athena"))
                or e0.has_real_attachments
                or (e0.category == "Börse/Aktien" and any(k in (e0.subject + ' ' + e0.body_snip).lower() for k in ["frist", "dring", "zahlung", "dividende", "earnings", "kursalarm"]))
            )
            if show_body and e0.body_snip:
                lines.append(f"    Inhalt: { _clip(e0.body_snip, 160) }")
            lines.append("")

        lines.append("")

    # Low-signal summaries
    for cat in LOW_SIGNAL_CATS:
        items = [e for e in by_cat.get(cat, [])]
        if not items:
            continue
        lines.append(cat.upper() + " (Zusammenfassung)")
        lines.append("-" * 72)
        sender_counts = Counter([e.from_addr or e.from_short for e in items])
        thread_counts = Counter([e.thread or e.subject.lower() for e in items])
        lines.append("Top Absender:")
        for s, c in sender_counts.most_common(8):
            lines.append(f"- {s} ({c})")
        lines.append("")
        lines.append("Top Threads:")
        for t, c in thread_counts.most_common(8):
            t_disp = (t[:80] + "…") if len(t) > 80 else t
            lines.append(f"- {t_disp} ({c})")
        lines.append("")

    lines.append("=" * 72)
    lines.append("Ende.")
    return "\n".join(lines).rstrip() + "\n"


def render_html(now: datetime, all_items: list[DigestEmail], imap_warnings: list[dict] | None = None) -> str:
    folder_counts = Counter([e.folder for e in all_items])
    cat_counts = Counter([e.category for e in all_items])
    imap_warnings = imap_warnings or []

    important_pool = [e for e in all_items if (e.category in ("Vermieter", "Börse/Aktien") and not e.suppressed)]
    highlights = extract_highlights(important_pool)

    def esc(s: str) -> str:
        return html.escape(s or "")

    chips = []
    chips.append(f"<span class='chip'><b>{len(all_items)}</b> Mails</span>")
    for k, v in folder_counts.items():
        chips.append(f"<span class='chip'>{esc(k)}: {v}</span>")
    for k, v in cat_counts.items():
        chips.append(f"<span class='chip'>{esc(k)}: {v}</span>")

    # build sections
    sections = []

    if imap_warnings:
        lis = "".join([
            f"<li><b>{esc(w.get('type','warning'))}</b>: {esc(w.get('hint',''))}<br><span style='color:#64748b'>UNSEEN: {esc(str(w.get('unseen',{})))}</span></li>"
            for w in imap_warnings[:5]
        ])
        sections.append(
            """
            <div class='section important'>
              <h2>Warnungen</h2>
              <ul class='list'>
            """
            + lis
            + "</ul></div>"
        )

    if highlights:
        lis = "".join([f"<li>{esc(h[2:])}</li>" for h in highlights])
        sections.append(
            """
            <div class='section important'>
              <h2>Wichtig (heuristisch)</h2>
              <ul class='list'>
            """
            + lis
            + "</ul></div>"
        )

    by_cat: dict[str, list[DigestEmail]] = defaultdict(list)
    for e in all_items:
        by_cat[e.category].append(e)

    # high signal detailed sections
    for cat in HIGH_SIGNAL_CATS:
        items = [e for e in by_cat.get(cat, []) if not e.suppressed]
        if not items:
            continue
        by_thread: dict[str, list[DigestEmail]] = defaultdict(list)
        for e in items:
            by_thread[e.thread].append(e)
        threads_sorted = sorted(
            by_thread.items(),
            key=lambda kv: max((x.timestamp for x in kv[1]), default=""),
            reverse=True,
        )

        rows = []
        for _t, group in threads_sorted[:15]:
            group_sorted = sorted(group, key=lambda x: x.timestamp, reverse=True)
            e0 = group_sorted[0]
            suffix = f" (+{len(group_sorted)-1} ähnlich)" if len(group_sorted) > 1 else ""
            show_body = (
                (e0.category in ("Vermieter", "Athena"))
                or e0.has_real_attachments
                or (e0.category == "Börse/Aktien" and any(k in (e0.subject + ' ' + e0.body_snip).lower() for k in ["frist", "dring", "zahlung", "dividende", "earnings", "kursalarm"]))
            )
            body = esc(_clip(e0.body_snip, 200)) if (show_body and e0.body_snip) else ""

            att_html = ""
            if e0.has_real_attachments:
                att_html = f"<div class='snip'><b>Anhang:</b> {esc(attachment_summary(e0))}</div>"
                pdf_prev = pdf_preview_from_maildir(e0)
                if pdf_prev:
                    att_html += f"<div class='snip'><b>PDF-Preview:</b> {esc(pdf_prev)}</div>"

            rows.append(
                f"""
                <div class='item'>
                  <div class='subject'><span class='tag'>{esc(e0.folder)}</span> {esc(e0.subject)}{esc(suffix)}</div>
                  <div class='meta'>{esc(e0.from_short)} · {esc(e0.timestamp)}</div>
                  {att_html}
                  {f"<div class='snip'>{body}</div>" if body else ""}
                </div>
                """
            )

        sections.append(
            f"<div class='section'><h2>{esc(cat)}</h2>" + "".join(rows) + "</div>"
        )

    # low signal summary sections
    for cat in LOW_SIGNAL_CATS:
        items = [e for e in by_cat.get(cat, [])]
        if not items:
            continue
        sender_counts = Counter([e.from_addr or e.from_short for e in items])
        thread_counts = Counter([e.thread or e.subject.lower() for e in items])

        sender_list = "".join(
            [f"<li>{esc(s)} <span class='count'>({c})</span></li>" for s, c in sender_counts.most_common(8)]
        )
        thread_list = "".join(
            [
                f"<li>{esc((t[:80] + '…') if len(t) > 80 else t)} <span class='count'>({c})</span></li>"
                for t, c in thread_counts.most_common(8)
            ]
        )

        sections.append(
            f"""
            <div class='section'>
              <h2>{esc(cat)} (Zusammenfassung)</h2>
              <div class='cols'>
                <div>
                  <h3>Top Absender</h3>
                  <ul class='list'>{sender_list}</ul>
                </div>
                <div>
                  <h3>Top Threads</h3>
                  <ul class='list'>{thread_list}</ul>
                </div>
              </div>
            </div>
            """
        )

    return f"""<!doctype html>
<html lang='de'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Landlord Digest</title>
  <style>
    body {{ font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background:#f4f6fb; margin:0; padding:16px; color:#0f172a; }}
    .card {{ max-width: 760px; margin: 0 auto; background:#fff; border-radius: 10px; overflow:hidden; box-shadow: 0 1px 6px rgba(0,0,0,.08); }}
    .header {{ background:#0b1220; color:#fff; padding:18px 22px; }}
    .header h1 {{ font-size:18px; margin:0; }}
    .header .meta {{ margin-top:4px; font-size:12px; color:#94a3b8; }}
    .chips {{ padding:12px 22px; background:#eef2ff; display:flex; flex-wrap:wrap; gap:8px; }}
    .chip {{ background:#dbeafe; color:#0f172a; border-radius: 999px; padding:4px 10px; font-size:12px; }}
    .section {{ padding: 14px 22px; }}
    h2 {{ margin:0 0 10px; font-size:14px; color:#334155; border-bottom:2px solid #e2e8f0; padding-bottom:6px; }}
    .important h2 {{ color:#b91c1c; border-bottom-color:#fecaca; }}
    .item {{ padding:10px 0; border-bottom:1px solid #f1f5f9; }}
    .item:last-child {{ border-bottom:none; }}
    .subject {{ font-weight:600; color:#0f172a; font-size:13px; }}
    .meta {{ color:#64748b; font-size:12px; margin-top:2px; }}
    .snip {{ color:#475569; font-size:12px; margin-top:6px; line-height:1.35; }}
    .tag {{ display:inline-block; font-size:10px; background:#e2e8f0; color:#334155; border-radius:6px; padding:2px 6px; margin-right:6px; text-transform:uppercase; }}
    .cols {{ display:flex; gap:18px; flex-wrap:wrap; }}
    .cols > div {{ flex: 1 1 300px; }}
    h3 {{ font-size:12px; margin:0 0 6px; color:#475569; }}
    ul.list {{ margin:0; padding-left:18px; color:#334155; font-size:12px; }}
    .count {{ color:#64748b; }}
    .footer {{ padding: 14px 22px; text-align:center; color:#94a3b8; font-size:11px; background:#fafafa; }}
  </style>
</head>
<body>
  <div class='card'>
    <div class='header'>
      <h1>Landlord Digest</h1>
      <div class='meta'>Erstellt (UTC): {now.strftime('%Y-%m-%d %H:%M')}Z</div>
    </div>
    <div class='chips'>{''.join(chips)}</div>
    {''.join(sections)}
    <div class='footer'>Athena · {len(all_items)} Mails verarbeitet</div>
  </div>
</body>
</html>"""


def send_telegram_digest(all_items: list) -> None:
    """Send short digest to Telegram."""
    import requests
    import os
    from pathlib import Path
    
    # Try to get Telegram credentials
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        # Try reading from FlightScanner config
        config_path = WORKSPACE / 'FlightScanner' / 'config.ini'
        if config_path.exists():
            import configparser
            config = configparser.ConfigParser()
            config.read(config_path)
            try:
                token = config.get('notifications', 'telegram_bot_token')
                chat_id = config.get('notifications', 'telegram_chat_id')
            except:
                pass
    
    if not token or not chat_id:
        print("Telegram not configured, skipping")
        return
    
    # Generate short digest
    by_category = {}
    for e in all_items:
        cat = e.category
        by_category.setdefault(cat, []).append(e)
    
    emoji = {
        "Vermieter": "🏠", 
        "Börse/Aktien": "📈",
        "Werbung/Newsletter": "📢", 
        "Sonstiges": "📧",
        "Athena": "🤖"
    }
    
    lines = ["📧 *Mail-Digest*", ""]
    for cat in ["Vermieter", "Börse/Aktien", "Athena", "Werbung/Newsletter", "Sonstiges"]:
        if cat in by_category:
            items = by_category[cat]
            lines.append(f"{emoji.get(cat, '📧')} *{cat}* ({len(items)})")
            for item in items[:3]:
                subj = item.subject[:40]
                if len(item.subject) > 40:
                    subj += "..."
                lines.append(f" • {subj}")
            if len(items) > 3:
                lines.append(f" ... +{len(items)-3} mehr")
            lines.append("")
    
    text = "\n".join(lines)
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram send error: {e}")


def send_email(cfg: dict, subject: str, text_body: str, html_body: str) -> None:
    smtp_cfg = cfg["smtp"]
    mail_cfg = cfg["mail"]

    msg = EmailMessage()
    msg["From"] = mail_cfg["from"]
    msg["To"] = mail_cfg["to"]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)

    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    host = smtp_cfg["host"]
    port = int(smtp_cfg.get("port", 587))
    user = smtp_cfg["user"]
    pw = smtp_cfg["pass"]

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo()
        if smtp_cfg.get("starttls", True):
            s.starttls()
            s.ehlo()
        s.login(user, pw)
        s.send_message(msg)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--from-json', default=None, help='Use messages from JSON file (backfill).')
    ap.add_argument('--subject-prefix', default='Mail TLTR (8h)', help='Email subject prefix.')
    ap.add_argument('--label', default=None, help='Optional label to include in subject (e.g., Abos batch).')
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    cfg = load_config()

    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding='utf-8'))
        messages = data.get('messages', [])
        imap_warnings = data.get('warnings', []) or []
        batch = data.get('batch') or {}
    else:
        # Fetch messages directly from IMAP for stability (no dependency on local Maildir sync).
        proc = subprocess.run(
            [sys.executable, str(WORKSPACE / 'scripts' / 'check_landlord_mail_imap.py')],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"IMAP fetch failed ({proc.returncode}): {proc.stderr[:400]}")

        data = json.loads(proc.stdout)
        messages = data.get("messages", [])
        imap_warnings = data.get('warnings', []) or []
        batch = {}

    rules = load_rules()

    # Hybrid classification (rules → phi → claude for uncertain) + cache
    judge_cache = judge_classify(messages, rules=rules, max_judge=6)

    all_items = [to_digest_email(m, judge_cache=judge_cache) for m in messages]

    text_body = render_text(now, all_items, imap_warnings=imap_warnings)
    html_body = render_html(now, all_items, imap_warnings=imap_warnings)

    label = f" – {args.label}" if args.label else ""
    if batch.get('uid_from') and batch.get('uid_to'):
        label += f" [UID {batch.get('uid_from')}-{batch.get('uid_to')}]"

    subject = f"{args.subject_prefix}{label} – {now.strftime('%Y-%m-%d %H:%M')}Z – {len(all_items)} neu"
    send_email(cfg, subject, text_body, html_body)
    send_telegram_digest(all_items)

    # stdout summary for automations
    out = {
        "sent_at": now.isoformat(),
        "message_count": len(all_items),
        "per_folder": dict(Counter([e.folder for e in all_items])),
        "per_category": dict(Counter([e.category for e in all_items])),
        "email_to": cfg["mail"]["to"],
        "email_from": cfg["mail"]["from"],
        "smtp_host": cfg["smtp"]["host"],
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        err = {
            "error": str(e),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(err, ensure_ascii=False))
        raise
