""""Analizuj z AI" — a one-paragraph, plain-language summary of what changed
recently for one domain.

The one rule this module is built around: **the model never sees raw data
and never does arithmetic.** Every number, date, and IP address that ends up
in the summary is computed here, in plain Python, from the same repositories
the rest of the dashboard already trusts — SPF/DKIM alignment trend, bounce
trend, blacklist persistence, and the domain's already-computed Flags. Those
facts are handed to Claude as finished, verified sentences with one job:
arrange them into a short paragraph. It cannot invent a number that was
never given to it, because it is never given anything else to work with.

If nothing notable happened, no facts are produced and the API is never
called — no cost, no risk, just "no notable changes".
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any, Dict, List, Optional

from .config import Settings, load_domains
from .health import domain_statuses
from .storage import BlacklistRepository, BounceRepository, Database, DmarcRepository

logger = logging.getLogger(__name__)

# A spike has to clear both bars — otherwise "1 fail became 2" reads as a
# dramatic doubling. Tuned for readability, not statistical rigor.
_MIN_SPIKE_RATIO = 2.0
_MIN_SPIKE_ABSOLUTE = 5

# Hardcoded rather than relying on the OS locale (strftime("%B")) — the same
# reason i18n.py never depends on system locale: it has to read right
# regardless of what's installed on whatever machine runs this.
_MONTHS_PL = [
    "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
]
_MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _format_day(day: Any, lang: str) -> str:
    """'2026-08-23' -> '23 sierpnia' (pl) / 'August 23' (en).

    Facts read as something a person would actually write, not a DB dump —
    and it keeps the model from having to reformat dates itself, which is
    exactly the kind of small transformation that invites a slip.
    """
    try:
        parsed = dt.date.fromisoformat(str(day)[:10])
    except ValueError:
        return str(day)
    if lang == "pl":
        return f"{parsed.day} {_MONTHS_PL[parsed.month - 1]}"
    return f"{_MONTHS_EN[parsed.month - 1]} {parsed.day}"

# Cache keyed by (domain, days, lang); avoids paying for the same summary on
# every accidental double-click. Plain dict + lock — this app is one process,
# one worker, so nothing fancier is needed.
_cache: Dict[tuple, Dict[str, Any]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = dt.timedelta(minutes=10)


def _localized_flags(settings: Settings, database: Database, domain: str, days: int, lang: str) -> List[Dict[str, Any]]:
    configured = {d.name: d for d in load_domains()}
    if domain not in configured:
        return []
    raw = domain_statuses(settings, database, domains=[configured[domain]], window_days=days)
    if not raw:
        return []
    return raw[0].localize(lang)["flags"]


def _spike_facts(rows: List[Dict[str, Any]], key: str, label_pl: str, label_en: str, lang: str) -> List[str]:
    """Day-over-day spikes in one alignment-failure series (spf_fail/dkim_fail)."""
    facts: List[str] = []
    rows = sorted(rows, key=lambda r: r["day"])
    prev_value: Optional[int] = None
    prev_day = None
    for row in rows:
        value = row.get(key) or 0
        day = row["day"]
        if prev_value is not None and value >= _MIN_SPIKE_ABSOLUTE and prev_value > 0:
            if value >= prev_value * _MIN_SPIKE_RATIO:
                if lang == "pl":
                    facts.append(
                        f"{label_pl} skoczyło z {prev_value} do {value} wiadomości, "
                        f"między {_format_day(prev_day, lang)} a {_format_day(day, lang)}."
                    )
                else:
                    facts.append(
                        f"{label_en} jumped from {prev_value} to {value} messages, "
                        f"between {_format_day(prev_day, lang)} and {_format_day(day, lang)}."
                    )
        elif prev_value is not None and prev_value >= _MIN_SPIKE_ABSOLUTE and value == 0:
            if lang == "pl":
                facts.append(f"{label_pl} spadło do zera {_format_day(day, lang)} (wcześniej {prev_value}).")
            else:
                facts.append(f"{label_en} dropped to zero on {_format_day(day, lang)} (was {prev_value}).")
        prev_value, prev_day = value, day
    return facts


def _bounce_spike_facts(rows: List[Dict[str, Any]], lang: str) -> List[str]:
    """Day-over-day spikes per bounce class (hard/soft/sender_block)."""
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_class.setdefault(row["bounce_class"], []).append(row)

    class_labels = {
        "sender_block": ("blokad nadawcy", "sender blocks"),
        "hard": ("twardych odbić", "hard bounces"),
        "soft": ("miękkich odbić", "soft bounces"),
        "unknown": ("nierozpoznanych odbić", "unclassified bounces"),
    }
    facts: List[str] = []
    for klass, class_rows in by_class.items():
        label_pl, label_en = class_labels.get(klass, (klass, klass))
        class_rows = sorted(class_rows, key=lambda r: r["day"])
        prev_value = None
        prev_day = None
        for row in class_rows:
            value = row.get("count") or 0
            day = row["day"]
            if prev_value is not None and value >= _MIN_SPIKE_ABSOLUTE and prev_value > 0 and value >= prev_value * _MIN_SPIKE_RATIO:
                if lang == "pl":
                    facts.append(
                        f"Liczba {label_pl} skoczyła z {prev_value} do {value}, "
                        f"między {_format_day(prev_day, lang)} a {_format_day(day, lang)}."
                    )
                else:
                    facts.append(
                        f"{label_en.capitalize()} jumped from {prev_value} to {value}, "
                        f"between {_format_day(prev_day, lang)} and {_format_day(day, lang)}."
                    )
            prev_value, prev_day = value, day
    return facts


def _blacklist_persistence_facts(rows: List[Dict[str, Any]], lang: str) -> List[str]:
    """IPs listed in every recent check round, not just the latest one."""
    by_ip: Dict[str, List[Dict[str, Any]]] = {}
    rounds = set()
    for row in rows:
        by_ip.setdefault(row["ip"], []).append(row)
        rounds.add(row["checked_at"])
    total_rounds = len(rounds)
    if total_rounds < 2:
        return []

    facts: List[str] = []
    for ip, ip_rows in by_ip.items():
        # Count how many rounds *each specific list* actually flagged this
        # IP — not "any list, in any listed round". Bundling those together
        # was the bug: a list that hit once got reported as if it applied to
        # every round just because a *different* list genuinely did.
        rounds_per_list: Dict[str, int] = {}
        for r in ip_rows:
            if not r["listed"]:
                continue
            for name in r.get("listed_by") or []:
                rounds_per_list[name] = rounds_per_list.get(name, 0) + 1

        persistent = sorted(name for name, count in rounds_per_list.items() if count == total_rounds)
        one_off = sorted(name for name, count in rounds_per_list.items() if 0 < count < total_rounds)

        if persistent:
            lists_text = ", ".join(persistent)
            if lang == "pl":
                facts.append(f"IP {ip} jest zablokowane w każdym z ostatnich {total_rounds} sprawdzeń ({lists_text}).")
            else:
                facts.append(f"IP {ip} has been listed in every one of the last {total_rounds} checks ({lists_text}).")

        # Worth surfacing but explicitly NOT as "persistent" — a one-off hit
        # reads very differently from a standing block.
        for name in one_off:
            count = rounds_per_list[name]
            if lang == "pl":
                facts.append(
                    f"IP {ip} pojawił się na liście {name} tylko w {count} z ostatnich {total_rounds} "
                    f"sprawdzeń — to nie jest konsekwentne zablokowanie, raczej pojedynczy incydent."
                )
            else:
                facts.append(
                    f"IP {ip} showed up on {name} in only {count} of the last {total_rounds} checks — "
                    f"not a standing block, more like a one-off incident."
                )
    return facts


def build_domain_facts(
    settings: Settings, database: Database, domain: str, days: int, lang: str
) -> List[str]:
    """Every fact is a finished, already-correct sentence — see module docstring."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    project_id = settings.project_id

    facts: List[str] = []

    for flag in _localized_flags(settings, database, domain, days, lang):
        facts.append(f"[{flag['severity']}] {flag['title']}: {flag['message']}")

    dmarc_repo = DmarcRepository(database, project_id)
    align_rows = dmarc_repo.daily_alignment_failures(since, domain)
    facts += _spike_facts(align_rows, "spf_fail", "SPF fail", "SPF fail", lang)
    facts += _spike_facts(align_rows, "dkim_fail", "DKIM fail", "DKIM fail", lang)

    bounce_repo = BounceRepository(database, project_id)
    bounce_rows = bounce_repo.daily_counts(since, domain)
    facts += _bounce_spike_facts(bounce_rows, lang)

    blacklist_repo = BlacklistRepository(database, project_id)
    blacklist_rows = blacklist_repo.history_for_domain(domain, since)
    facts += _blacklist_persistence_facts(blacklist_rows, lang)

    return facts


_SYSTEM_PL = (
    "Jesteś asystentem podsumowującym monitoring deliverability e-mail. Dostajesz listę "
    "już zweryfikowanych faktów — każda liczba, data i adres IP w tej liście została policzona "
    "przez inny, deterministyczny system, nie przez Ciebie. Nie masz dostępu do żadnych innych "
    "danych o tej domenie.\n\n"
    "Napisz zwięzłe podsumowanie po polsku, skupione na tym co się zmieniło i co jest "
    "najpilniejsze. Zasady, bez wyjątków:\n"
    "- Używaj WYŁĄCZNIE liczb, dat i adresów IP z podanej listy — nigdy niczego nie dopisuj ani nie zaokrąglaj inaczej niż podano.\n"
    "- Daty w liście są już zapisane po ludzku (np. '23 sierpnia') — przepisuj je dokładnie tak, nigdy nie zamieniaj z powrotem na format RRRR-MM-DD.\n"
    "- Nie zgaduj przyczyn, których nie ma na liście.\n"
    "- Formatuj dla czytelności: **pogrubiaj** (podwójne gwiazdki) najważniejsze liczby, adresy IP i nazwy dostawców; "
    "oddzielaj różne wątki pustą linią, żeby każdy temat był osobnym, krótkim akapitem (1-2 zdania) — nie jedną ścianą tekstu.\n"
    "- Jeśli fakty się ze sobą nie łączą w spójną historię, po prostu wymień je krótko, każdy w osobnym akapicie.\n"
    "- Jeśli lista jest pusta, napisz jednym zdaniem, że nie ma nic wartego uwagi.\n"
    "- Nie dodawaj własnego tytułu ani nagłówka (np. '# Podsumowanie') — pole nad Twoim tekstem już ma tytuł. Zacznij od razu od treści."
)

_SYSTEM_EN = (
    "You summarize email deliverability monitoring. You receive a list of already-verified "
    "facts — every number, date, and IP address in this list was computed by a separate, "
    "deterministic system, not by you. You have no access to any other data about this domain.\n\n"
    "Write a concise summary in English, focused on what changed and what's most urgent. "
    "Rules, no exceptions:\n"
    "- Use ONLY the numbers, dates, and IP addresses given in the list — never add or round anything differently than given.\n"
    "- Dates in the list are already written naturally (e.g. 'August 23') — copy them exactly, never convert back to YYYY-MM-DD.\n"
    "- Never guess a cause that isn't in the list.\n"
    "- Format for readability: **bold** (double asterisks) the key numbers, IP addresses, and provider names; "
    "separate distinct topics with a blank line so each is its own short paragraph (1-2 sentences), not one wall of text.\n"
    "- If the facts don't connect into one story, just list them briefly, each in its own paragraph.\n"
    "- If the list is empty, say in one sentence that there's nothing notable.\n"
    "- Don't add your own title or heading (e.g. '# Summary') — the box above your text already has one. Start straight into the content."
)


def _call_claude(facts: List[str], lang: str) -> str:
    import anthropic  # imported lazily so the rest of the app works without the package/key

    client = anthropic.Anthropic()
    system = _SYSTEM_PL if lang == "pl" else _SYSTEM_EN
    facts_block = "\n".join(f"- {f}" for f in facts)
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system=system,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": facts_block}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return text.strip()


def summarize_domain(settings: Settings, database: Database, domain: str, days: int, lang: str) -> Dict[str, Any]:
    """Returns ``{"summary": str, "facts_count": int, "cached": bool}``.

    Never raises — API/config failures come back as a message key the
    template can show, same convention as ``validate.py``.
    """
    lang = lang if lang in ("pl", "en") else "pl"
    cache_key = (domain, days, lang)

    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and dt.datetime.now(dt.timezone.utc) - cached["at"] < _CACHE_TTL:
            return {"summary": cached["summary"], "facts_count": cached["facts_count"], "cached": True}

    facts = build_domain_facts(settings, database, domain, days, lang)

    if not facts:
        summary = "Brak istotnych zmian w tym okresie." if lang == "pl" else "No notable changes in this period."
        result = {"summary": summary, "facts_count": 0, "cached": False}
        with _cache_lock:
            _cache[cache_key] = {"summary": summary, "facts_count": 0, "at": dt.datetime.now(dt.timezone.utc)}
        return result

    try:
        summary = _call_claude(facts, lang)
    except Exception as exc:  # noqa: BLE001 — surfaced to the UI, never a 500
        logger.exception("AI summary failed for %s", domain)
        summary = (
            f"Nie udało się wygenerować podsumowania AI ({exc})."
            if lang == "pl"
            else f"Could not generate the AI summary ({exc})."
        )
        return {"summary": summary, "facts_count": len(facts), "cached": False, "error": True}

    with _cache_lock:
        _cache[cache_key] = {"summary": summary, "facts_count": len(facts), "at": dt.datetime.now(dt.timezone.utc)}
    return {"summary": summary, "facts_count": len(facts), "cached": False}
