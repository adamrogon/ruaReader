"""Translations for the dashboard.

The dashboard is bilingual (PL/EN); everything the ingestion layer decides to
say — flag titles, DNS warnings, ingestion-freshness messages — is produced as
a ``(key, params)`` pair rather than a finished sentence, so the language is
resolved once, at render time, from a single dictionary here. Nothing upstream
of the web layer needs to know which language is active.

Parts that are inherently technical and language-neutral (SPF record text, raw
DNS resolver errors, SMTP diagnostic codes) are passed through as params and
are not translated — only the sentence structure around them is.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANG = "pl"
SUPPORTED_LANGS = ("pl", "en")


_NESTED_MARKER = "__nested__"


def Nested(key: Any, **params: Any) -> dict:
    """Mark a message param whose own value needs translating.

    Used when part of a sentence is picked from a small set of variants that
    are themselves language-dependent — e.g. singular/plural wording, or which
    of several explanatory clauses applies. ``Nested(None)`` resolves to an
    empty string, for the common case of "this clause only applies sometimes".

    Deliberately a plain dict rather than a custom class: some of these params
    travel through a JSON database column (DNS check warnings) between being
    built and being translated, and a custom object would not survive that
    round-trip. :func:`translate` resolves one level of these before
    formatting the outer template; nesting is intentionally shallow.
    """
    return {_NESTED_MARKER: True, "key": key, "params": params}


def _is_nested(value: Any) -> bool:
    return isinstance(value, dict) and value.get(_NESTED_MARKER) is True

# key -> {"pl": template, "en": template}, using str.format() placeholders.
MESSAGES: dict = {
    # --- Chrome / navigation ------------------------------------------------
    "app.title": {"pl": "Monitor dostarczalności", "en": "Deliverability Monitor"},
    "app.brand_sub": {"pl": "Linkhouse", "en": "Linkhouse"},
    "nav.overview": {"pl": "Przegląd", "en": "Overview"},
    "nav.settings": {"pl": "Ustawienia", "en": "Settings"},
    "nav.soon": {"pl": "Wkrótce", "en": "Coming soon"},
    "lang.switch_to": {"pl": "English", "en": "Polski"},
    "range.label": {"pl": "Zakres dat", "en": "Date range"},
    "range.day": {"pl": "1 dzień", "en": "1 day"},
    "range.days": {"pl": "{n} dni", "en": "{n} days"},

    # --- Overview page --------------------------------------------------------
    "overview.title": {"pl": "Domeny wysyłkowe", "en": "Sending domains"},
    "overview.subtitle": {
        "pl": "Ułożone wg pilności — domena wymagająca najpilniejszej uwagi jest pierwsza. Ostatnie {days} dni.",
        "en": "Ordered by urgency — the domain most in need of attention is first. Last {days} days.",
    },
    "kpi.sender_blocks": {"pl": "Blokady nadawcy", "en": "Sender blocks"},
    "kpi.blacklisted": {"pl": "Na blackliście", "en": "Blacklisted"},
    "kpi.critical": {"pl": "Krytyczne", "en": "Critical"},
    "kpi.warning": {"pl": "Ostrzeżenia", "en": "Warning"},
    "kpi.healthy": {"pl": "Zdrowe", "en": "Healthy"},
    "kpi.messages": {"pl": "Wiadomości", "en": "Messages"},

    "health.stale_note": {
        "pl": "Część danych jest nieaktualna.",
        "en": "Some data is not current.",
    },
    "section.domains": {"pl": "Domeny", "en": "Domains"},
    "section.domains_hint": {"pl": "najpilniejsze pierwsze", "en": "worst first"},
    "section.by_provider": {"pl": "Wg dostawcy odbierającego", "en": "By receiving provider"},
    "section.by_provider_hint": {
        "pl": "który dostawca ma problem i z którymi domenami",
        "en": "which provider has a problem, and with which domains",
    },
    "section.trends": {"pl": "Trendy", "en": "Trends"},

    "table.provider": {"pl": "Dostawca", "en": "Provider"},
    "table.messages": {"pl": "Wiadomości", "en": "Messages"},
    "table.disposition": {"pl": "Dyspozycja", "en": "Disposition"},
    "disposition.reject": {"pl": "odrzucono", "en": "rejected"},
    "disposition.quarantine": {"pl": "kwarantanna", "en": "quarantined"},
    "disposition.none": {"pl": "przepuszczono", "en": "delivered anyway"},
    "table.compliance": {"pl": "Zgodność", "en": "Compliance"},
    "table.passed": {"pl": "Zaliczone", "en": "Passed"},
    "table.failed": {"pl": "Niezaliczone", "en": "Failed"},
    "table.forwarded": {"pl": "Przekierowane", "en": "Forwarded"},
    "table.domains_with_failures": {"pl": "Domeny z niezaliczonymi", "en": "Domains with failures"},
    "table.none": {"pl": "brak", "en": "none"},

    "metric.messages": {"pl": "Wiadomości", "en": "Messages"},
    "metric.compliance": {"pl": "Zgodność", "en": "Compliance"},
    "metric.bounces": {"pl": "Odbicia", "en": "Bounces"},
    "metric.spf_lookups": {"pl": "Zapytania SPF", "en": "SPF lookups"},

    "badge.ok": {"pl": "ok", "en": "ok"},
    "badge.healthy": {"pl": "zdrowa", "en": "healthy"},
    "badge.blocked": {"pl": "zablokowana", "en": "blocked"},
    "badge.blacklisted": {"pl": "na blackliście", "en": "blacklisted"},

    "chart.volume.title": {"pl": "Dzienny wolumen wg wyniku", "en": "Daily volume by outcome"},
    "chart.volume.sub": {
        "pl": "Przekierowana poczta pokazana osobno, nie liczy się jako błąd.",
        "en": "Forwarded mail is shown separately, not counted as failure.",
    },
    "chart.bounces.title": {"pl": "Dzienne odbicia wg klasy", "en": "Daily bounces by class"},
    "chart.bounces.sub": {
        "pl": "Blokady nadawcy to najważniejszy sygnał.",
        "en": "Sender blocks are the signal that matters most.",
    },
    "chart.legend.passed": {"pl": "Zaliczone", "en": "Passed"},
    "chart.legend.forwarded": {"pl": "Przekierowane", "en": "Forwarded"},
    "chart.legend.failed": {"pl": "Niezaliczone", "en": "Failed"},
    "chart.legend.compliance": {"pl": "Zgodność %", "en": "Compliance %"},
    "chart.legend.sender_block": {"pl": "Blokada nadawcy", "en": "Sender block"},
    "chart.legend.hard": {"pl": "Twarde", "en": "Hard"},
    "chart.legend.soft": {"pl": "Miękkie", "en": "Soft"},
    "chart.legend.unparsed": {"pl": "Nieprzetworzone", "en": "Unparsed"},
    "chart.axis.messages": {"pl": "wiadomości", "en": "messages"},
    "chart.axis.compliance": {"pl": "zgodność %", "en": "compliance %"},
    "chart.axis.bounces": {"pl": "odbicia", "en": "bounces"},

    "empty.no_domains": {
        "pl": "Brak skonfigurowanych domen. Dodaj je w",
        "en": "No domains configured. Add them to",
    },
    "empty.no_report_data": {"pl": "Brak danych z raportów w tym zakresie.", "en": "No report data in this window."},
    "empty.no_bounces": {"pl": "Brak odbić w tym zakresie.", "en": "No bounces in this window."},
    "empty.nothing_flagged": {
        "pl": "Nic nie zgłoszono dla tej domeny w ostatnie {days} dni.",
        "en": "Nothing flagged for this domain in the last {days} days.",
    },
    "empty.dns_not_checked": {
        "pl": "Brak jeszcze zapisanego sprawdzenia DNS. Uruchom",
        "en": "No DNS check recorded yet. Run",
    },

    "footer.note": {
        "pl": "Wskaźniki pokazane są osobno celowo — nie ma jednej łącznej oceny zdrowia. "
        "Przekierowana poczta jest wyłączona ze wskaźnika zgodności.",
        "en": "Indicators are shown separately by design — there is no single combined health score. "
        "Forwarded mail is excluded from compliance rates.",
    },

    # --- Domain detail page ------------------------------------------------
    "domain.back": {"pl": "‹ Wszystkie domeny", "en": "‹ All domains"},
    "section.attention": {"pl": "Co wymaga uwagi", "en": "What needs attention"},
    "section.attention_hint": {"pl": "od najpoważniejszych", "en": "most serious first"},
    "section.sender_blocks": {"pl": "Blokady nadawcy", "en": "Sender blocks"},
    "section.sender_blocks_hint": {
        "pl": "odrzucenie domeny, nie pojedynczych adresów",
        "en": "rejections of the domain, not of individual addresses",
    },
    "section.bounce_codes": {"pl": "Kody odbić", "en": "Bounce codes"},
    "section.bounce_summary": {
        "pl": "Podsumowanie odbić",
        "en": "Bounce summary",
    },
    "section.bounce_summary_hint": {
        "pl": "jedna pozycja na każdą kombinację kodu, klasy i dostawcy — od najczęstszych",
        "en": "one row per code+class+provider combination — most frequent first",
    },
    "table.latest": {"pl": "Ostatnio", "en": "Latest"},
    "pager.showing": {
        "pl": "Strona {page} z {total_pages} · {total} wpisów łącznie",
        "en": "Page {page} of {total_pages} · {total} entries total",
    },
    "pager.prev": {"pl": "‹ Poprzednia", "en": "‹ Previous"},
    "pager.next": {"pl": "Następna ›", "en": "Next ›"},
    "section.dns_records": {"pl": "Rekordy DNS", "en": "DNS records"},
    "section.dns_checked_at": {"pl": "sprawdzono {when}", "en": "checked {when}"},
    "section.failing_sources": {
        "pl": "Źródła z największym wolumenem błędów",
        "en": "Highest-volume failing sources",
    },
    "section.failing_sources_hint": {
        "pl": "adresy IP wysyłające jako Twoja domena, ale bez ważnego SPF/DKIM — typowo zapomniany "
        "dawny dostawca, nowa usługa której nie dodano do SPF, albo ktoś podszywający się. Dane "
        "z raportów DMARC.",
        "en": "IPs sending as your domain but without valid SPF/DKIM — typically a forgotten former "
        "provider, a new service not yet in SPF, or someone spoofing you. Sourced from DMARC reports.",
    },

    "kpi.forwarded": {"pl": "Przekierowane", "en": "Forwarded"},
    "kpi.hard_bounces": {"pl": "Twarde odbicia", "en": "Hard bounces"},
    "kpi.dmarc_policy": {"pl": "Polityka DMARC", "en": "DMARC policy"},

    "table.when": {"pl": "Kiedy", "en": "When"},
    "table.code": {"pl": "Kod", "en": "Code"},
    "table.diagnostic": {"pl": "Diagnostyka", "en": "Diagnostic"},
    "table.class": {"pl": "Klasa", "en": "Class"},
    "table.count": {"pl": "Liczba", "en": "Count"},
    "table.source_ip": {"pl": "IP źródłowe", "en": "Source IP"},
    "table.host": {"pl": "Host", "en": "Host"},
    "table.dkim": {"pl": "DKIM", "en": "DKIM"},
    "table.spf": {"pl": "SPF", "en": "SPF"},
    "table.unparsed": {"pl": "nieprzetworzone", "en": "unparsed"},

    "bounce_class.hard": {"pl": "twarde", "en": "hard"},
    "bounce_class.soft": {"pl": "miękkie", "en": "soft"},
    "bounce_class.sender_block": {"pl": "blokada nadawcy", "en": "sender block"},
    "bounce_class.unknown": {"pl": "nieznane", "en": "unknown"},

    "dns.spf": {"pl": "SPF", "en": "SPF"},
    "dns.dmarc": {"pl": "DMARC", "en": "DMARC"},
    "dns.dkim": {"pl": "DKIM", "en": "DKIM"},
    "dns.mx": {"pl": "MX", "en": "MX"},
    "dns.missing": {"pl": "brak", "en": "missing"},
    "dns.none": {"pl": "brak", "en": "none"},
    "dns.lookups_of_limit": {"pl": "{count}/{limit} zapytań DNS", "en": "{count}/{limit} DNS lookups"},
    "dns.rua_set": {"pl": "rua ustawione", "en": "rua set"},
    "dns.no_rua": {"pl": "brak rua", "en": "no rua"},
    "dns.no_selectors": {
        "pl": "Brak skonfigurowanych selektorów dla tej domeny.",
        "en": "No selectors configured for this domain.",
    },
    "dns.selector_missing_suffix": {"pl": " brak", "en": " missing"},

    "run_hint.dns": {"pl": "python -m deliverability.cli dns", "en": "python -m deliverability.cli dns"},

    # --- Source labels shown on flags --------------------------------------
    "source.bounce": {"pl": "odbicia", "en": "bounce"},
    "source.dnsbl": {"pl": "blacklisty", "en": "dnsbl"},
    "source.dns": {"pl": "dns", "en": "dns"},
    "source.rua": {"pl": "raporty dmarc", "en": "rua"},

    # --- Severity labels -----------------------------------------------------
    "severity.critical": {"pl": "krytyczne", "en": "critical"},
    "severity.warning": {"pl": "ostrzeżenie", "en": "warning"},
    "severity.info": {"pl": "info", "en": "info"},
    "severity.ok": {"pl": "ok", "en": "ok"},

    # --- Ingestion freshness (health.ingestion_health) -----------------------
    "stream.rua": {"pl": "Raporty DMARC", "en": "DMARC reports"},
    "stream.bounce": {"pl": "Odbicia / NDR", "en": "Bounces / NDRs"},
    "stream.dns": {"pl": "Sprawdzenia DNS", "en": "DNS checks"},
    "stream.dnsbl": {"pl": "Sprawdzenia blacklist", "en": "Blacklist checks"},

    "ingest.never_run": {
        # A "{label}:" prefix sidesteps Polish case agreement (embedding the
        # label as a sentence object would need it in genitive case, which a
        # single label string can't provide for every stream at once).
        "pl": "{label}: ingestia nigdy nie zakończyła się powodzeniem. Dopóki się nie uda, ta część "
        "dashboardu jest po prostu pusta, a nie zdrowa.",
        "en": "{label} ingestion has never completed successfully. Until it runs, this section of "
        "the dashboard is empty rather than healthy.",
    },
    "ingest.stale": {
        "pl": "{label}: brak udanej ingestii od {hours:.0f} godzin (limit {limit}h). Dane poniżej są "
        "nieaktualne — traktuj je jako niezweryfikowane.",
        "en": "{label}: no successful ingestion for {hours:.0f} hours (limit {limit}h). The figures "
        "below are out of date — treat them as unverified.",
    },
    "ingest.ok": {
        "pl": "Ostatnie udane uruchomienie {hours:.1f} godz. temu.",
        "en": "Last successful run {hours:.1f} hours ago.",
    },
    "ingest.erroring": {
        "pl": "Ostatnie uruchomienie zakończyło się błędem: {error}",
        "en": "The most recent run failed: {error}",
    },
    "ingest.state.ok": {"pl": "{hours:.0f}h temu", "en": "{hours:.0f}h ago"},
    "ingest.state.stale": {"pl": "nieaktualne ({hours:.0f}h)", "en": "stale ({hours:.0f}h)"},
    "ingest.state.never_run": {"pl": "nigdy nie uruchomiono", "en": "never run"},
    "ingest.state.erroring": {"pl": "błąd", "en": "erroring"},

    # --- Flags: sender block --------------------------------------------------
    "flag.sender_block.title": {
        "pl": "Zablokowany nadawca przez {esp} ({count} odrzuceń)",
        "en": "Sender blocked by {esp} ({count} rejection(s))",
    },
    "flag.sender_block.message": {
        "pl": "{esp} odrzuca pocztę z tej domeny{codes_suffix}. To odrzucenie samego nadawcy, "
        "nie pojedynczych adresów, więc dotyczy wszystkiego wysyłanego z tej domeny.\n\n"
        "**Co zrobić:** wstrzymaj wysyłkę z tej domeny na 24–48 h. Sprawdź w logu odbić poniżej "
        "pełną treść odrzucenia — dostawca zwykle w niej pisze przyczynę (reputacja IP, treść, "
        "brak uwierzytelnienia). Jeśli ten sam kod pojawia się u innych dostawców, problem jest "
        "po Twojej stronie. Jeśli tylko u {esp} — zajrzyj do panelu tego dostawcy "
        "(Google Postmaster Tools, Microsoft SNDS).",
        "en": "{esp} is refusing mail from this domain{codes_suffix}. This is a rejection of the "
        "sender, not of individual addresses, so it affects everything sent from here.\n\n"
        "**What to do:** pause sending from this domain for 24–48 h. Read the full rejection in "
        "the bounce log below — providers usually spell out the reason (IP reputation, content, "
        "missing authentication). If the same code shows up at other providers too, the problem "
        "is on your side. If it's only {esp}, check that provider's postmaster panel "
        "(Google Postmaster Tools, Microsoft SNDS).",
    },
    "flag.sender_block.codes_suffix": {"pl": " z kodem {codes}", "en": " with {codes}"},
    "flag.sender_block.title_other": {
        "pl": "Zablokowany nadawca przez {esp} ({count} odrzuceń)",
        "en": "Sender blocked by {esp} ({count} rejection(s))",
    },
    "flag.sender_block.message_other": {
        "pl": "{esp} również odrzuca pocztę z tej domeny.",
        "en": "{esp} is also refusing mail from this domain.",
    },
    # "Minor" variant used when the rejecting ESP is not one of the majors —
    # same signal, calmer wording, warning severity instead of critical.
    "flag.sender_block.title_minor": {
        "pl": "Odrzucenie u {esp} ({count} razy)",
        "en": "Rejected by {esp} ({count}×)",
    },
    "flag.sender_block.message_minor": {
        "pl": "{esp} odrzucił pocztę z tej domeny{codes_suffix}. **To pojedynczy niszowy dostawca**, "
        "nie duży odbiorca (Google/Microsoft/Yahoo), więc nie ma powodu wstrzymywać wysyłki na "
        "podstawie samego tego sygnału.\n\n"
        "**Co zrobić:** zajrzyj do logu odbić poniżej, jeśli chcesz zrozumieć powód (często to lokalna "
        "polityka odbiorcy: whitelist, custom filtr). Zacznij działać dopiero jeśli ten sam kod "
        "pojawi się u któregoś z dużych dostawców.",
        "en": "{esp} refused mail from this domain{codes_suffix}. **This is a single niche provider**, "
        "not a major receiver (Google/Microsoft/Yahoo), so there's no reason to pause sending on "
        "the strength of this signal alone.\n\n"
        "**What to do:** check the bounce log below if you want to understand why (often a local "
        "policy of the receiver: whitelist, custom filter). Only start acting on it if the same "
        "code shows up at one of the major providers.",
    },

    # --- Flags: blacklist ------------------------------------------------------
    "flag.blacklist.title": {
        "pl": "{ip_count} adres(y) IP na {list_count} blackliście(ach)",
        "en": "{ip_count} sending IP(s) on {list_count} blacklist(s)",
    },
    "flag.blacklist.message": {
        "pl": "IP {ips} figuruje na: {names}. {detail}",
        "en": "IP {ips} listed by {names}. {detail}",
    },
    "flag.blacklist.detail_high_signal": {
        "pl": "{names} to lista, na którą dostawcy realnie reagują — spodziewaj się odrzucania lub "
        "trafiania do spamu u użytkowników tego dostawcy.\n\n"
        "**Co zrobić:** wejdź na stronę operatora listy (np. spamhaus.org/lookup, "
        "barracudacentral.org/rbl), wpisz IP i użyj formularza delisting. Zanim to zrobisz, znajdź "
        "przyczynę — jeśli IP wciąż wysyła spam, delisting nic nie da, wróci na listę w ciągu godzin. "
        "Sprawdź czy z tego IP nie wysyła inny klient/proces (najczęstsza przyczyna przy hostingu).",
        "en": "{names} is a list receivers act on directly — expect mail to this provider's users "
        "to be rejected or junked.\n\n"
        "**What to do:** go to the list operator's site (e.g. spamhaus.org/lookup, "
        "barracudacentral.org/rbl), enter the IP and use their delisting form. Before you do, "
        "find the cause — if the IP is still sending spam, delisting won't stick and you'll be "
        "back on the list within hours. Check that no other client/process is sending from that IP "
        "(the most common cause on shared hosting).",
    },
    "flag.blacklist.detail_netblock_wide": {
        "pl": "Wszystkie te wpisy pochodzą z {names}, która listuje **całe pule adresów lub ASN**, "
        "nie pojedynczych nadawców. Zwykle odzwierciedla to sąsiadów u dostawcy hostingu, nie Twoją "
        "wysyłkę.\n\n"
        "**Co zrobić:** zwykle **nic**. Większość dużych odbiorców (Google, Microsoft) tego nie "
        "uwzględnia. Zanim ruszysz cokolwiek, sprawdź te IP na spamhaus.org/lookup i "
        "mxtoolbox.com/blacklists — jeśli tam czysto, tę flagę można świadomie zignorować. Jeśli IP "
        "należy do serwera www (nie skrzynki wysyłkowej), rozważ usunięcie mechanizmu **a** z "
        "rekordu SPF — narzędzie przestanie sprawdzać ten adres.",
        "en": "All of these listings are from {names}, which lists **whole netblocks or ASNs** "
        "rather than individual senders. That usually reflects the hosting provider's neighbours "
        "rather than your own sending.\n\n"
        "**What to do:** usually **nothing**. Most large receivers (Google, Microsoft) do not act "
        "on these lists. Cross-check the IPs at spamhaus.org/lookup and mxtoolbox.com/blacklists "
        "— if they're clean there, you can consciously ignore this flag. If the IP belongs to a "
        "web server (not your sending mailbox), consider removing the **a** mechanism from your "
        "SPF record — the tool will stop checking that address.",
    },
    "flag.blacklist.detail_mixed": {
        "pl": "Odbiorcy sprawdzający te listy mogą odrzucać lub filtrować pocztę z tego IP.\n\n"
        "**Co zrobić:** sprawdź, o które listy chodzi — różnią się popularnością. Spamhaus, "
        "Barracuda, SpamCop znaczą dużo. Mniejsze listy często można zignorować. Zacznij od "
        "wpisania IP na mxtoolbox.com/blacklists — pokazuje szeroki przekrój i mówi, które są istotne.",
        "en": "Receivers consulting these lists may reject or junk mail from this IP.\n\n"
        "**What to do:** check which lists are involved — they vary widely in weight. Spamhaus, "
        "Barracuda, SpamCop are significant. Smaller lists can often be ignored. Start by looking "
        "up the IP at mxtoolbox.com/blacklists — it shows a broad set and flags which ones matter.",
    },

    # --- Flags: DNS ------------------------------------------------------------
    "flag.dns.no_spf.title": {"pl": "Brak rekordu SPF", "en": "No SPF record"},
    "flag.dns.no_spf.message": {
        "pl": "Ta domena nie publikuje rekordu SPF, więc odbiorcy nie mają listy autoryzowanych "
        "nadawców. Poczta z niej dużo częściej trafia do filtrów.\n\n"
        "**Co zrobić:** dodaj w DNS domeny rekord TXT wskazujący, przez kogo wysyłasz. Dla samego "
        "Google Workspace: `v=spf1 include:_spf.google.com ~all`. Dla Microsoft 365: "
        "`v=spf1 include:spf.protection.outlook.com ~all`. Jeśli używasz więcej dostawców — "
        "wszystkie w jednym rekordzie oddzielone spacją. Propagacja to zwykle godziny.",
        "en": "This domain publishes no SPF record, so receivers have no list of authorised "
        "senders. Mail from it is far more likely to be filtered.\n\n"
        "**What to do:** add a TXT record on the domain naming who sends for you. For Google "
        "Workspace alone: `v=spf1 include:_spf.google.com ~all`. For Microsoft 365: "
        "`v=spf1 include:spf.protection.outlook.com ~all`. If you use several providers, list "
        "all their includes separated by spaces in one record. Propagation is usually hours.",
    },
    "flag.dns.spf_over_limit.title": {
        "pl": "SPF przekracza limit zapytań DNS ({count}/{limit})",
        "en": "SPF exceeds the DNS lookup limit ({count}/{limit})",
    },
    "flag.dns.spf_over_limit.message": {
        "pl": "Ocena SPF wymaga {count} zapytań DNS, a limit z RFC to {limit}. Odbiorcy zwracają "
        "**permerror** i traktują SPF jako niezaliczone, niezależnie od tego, jak poprawna jest "
        "reszta rekordu.\n\n"
        "**Co zrobić:** rozwiń każde `include:` na jego zawartość i sprawdź, który zjada najwięcej "
        "(mxtoolbox.com/spf ma dobry visualizer). Usuń mechanizm `a` jeśli nie wysyłasz z serwera "
        "www. Rozważ **SPF flattening** — narzędzia jak dmarcian albo EasyDMARC generują wersję z "
        "wpisanymi `ip4:` zamiast `include:`, obniżając licznik do 0–1. To automatyzuje się cronem.",
        "en": "SPF evaluation needs {count} DNS lookups but the RFC limit is {limit}. Receivers "
        "return **permerror** and treat SPF as failed, however correct the rest of the record is.\n\n"
        "**What to do:** expand each `include:` and see which costs the most (mxtoolbox.com/spf "
        "has a good visualiser). Drop the `a` mechanism if you don't send from a web server. "
        "Consider **SPF flattening** — tools like dmarcian or EasyDMARC generate a version with "
        "explicit `ip4:` instead of `include:`, taking the count down to 0–1. Can be automated.",
    },
    "flag.dns.spf_near_limit.title": {
        "pl": "SPF blisko limitu zapytań ({count}/{limit})",
        "en": "SPF close to the lookup limit ({count}/{limit})",
    },
    "flag.dns.spf_near_limit.message": {
        "pl": "SPF zużywa obecnie {count} z {limit} dozwolonych zapytań DNS. Dodanie kolejnej "
        "usługi wysyłkowej prawdopodobnie to zepsuje.\n\n"
        "**Co zrobić:** zanim dodasz kolejny `include:`, sprawdź czy któryś z obecnych nie jest "
        "już nieużywany (stary dostawca, po którym nie posprzątano). Alternatywa jak wyżej — "
        "**SPF flattening** obniża licznik prawie do zera.",
        "en": "SPF currently costs {count} of the {limit} permitted DNS lookups. Adding one more "
        "sending service will likely break it.\n\n"
        "**What to do:** before adding another `include:`, check whether any current one is stale "
        "(a former provider not cleaned up). Alternative as above — **SPF flattening** takes the "
        "count down to almost zero.",
    },
    "flag.dns.spf_error.title": {"pl": "Problem z rekordem SPF", "en": "SPF record problem"},
    "flag.dns.no_dmarc.title": {"pl": "Brak rekordu DMARC", "en": "No DMARC record"},
    "flag.dns.no_dmarc.message": {
        "pl": "Bez rekordu DMARC nie powstają żadne raporty zbiorcze, więc ta domena jest "
        "niewidoczna dla reszty tego narzędzia.\n\n"
        "**Co zrobić:** dodaj w DNS domeny rekord TXT pod `_dmarc.<domena>`: "
        "`v=DMARC1; p=none; rua=mailto:<Twoja skrzynka rua>`. Zacznij od `p=none` (tylko "
        "monitorowanie), przez kilka tygodni obserwuj raporty, dopiero potem podnoś do "
        "`quarantine` i `reject`. Podniesienie za wcześnie wyśle Twoją legalną pocztę do spamu.",
        "en": "Without a DMARC record no aggregate reports are produced, so this domain is "
        "invisible to the rest of this tool.\n\n"
        "**What to do:** add a TXT record on the domain at `_dmarc.<domain>`: "
        "`v=DMARC1; p=none; rua=mailto:<your rua mailbox>`. Start with `p=none` (monitor only), "
        "watch reports for several weeks, then step up to `quarantine` and `reject`. Rushing this "
        "will send your legitimate mail to spam.",
    },
    "flag.dns.dmarc_no_rua.title": {"pl": "DMARC bez adresu rua=", "en": "DMARC has no rua= address"},
    "flag.dns.dmarc_no_rua.message": {
        "pl": "Rekord DMARC nie prosi o raporty zbiorcze, więc żadne dane nigdy nie napłyną dla "
        "tej domeny.\n\n"
        "**Co zrobić:** dopisz do rekordu DMARC `rua=mailto:<skrzynka>` — najlepiej jedna centralna "
        "skrzynka dla wszystkich Twoich domen (np. dmarc@<Twoja firma>). Jeśli skrzynka jest na "
        "innej domenie niż ta wysyłkowa, musisz jeszcze na domenie skrzynki dodać rekord "
        "autoryzacyjny: `<domena wysyłkowa>._report._dmarc.<domena skrzynki> TXT v=DMARC1` — bez "
        "tego Google i Microsoft odmówią wysyłania raportów.",
        "en": "The DMARC record does not ask for aggregate reports, so no data will ever arrive "
        "for this domain.\n\n"
        "**What to do:** add `rua=mailto:<mailbox>` to the DMARC record — ideally one central "
        "mailbox for all your domains (e.g. dmarc@<your company>). If the mailbox is on a "
        "different domain than the sending one, add an authorisation record on the mailbox's "
        "domain too: `<sending domain>._report._dmarc.<mailbox domain> TXT v=DMARC1` — without it "
        "Google and Microsoft refuse to send reports.",
    },
    "flag.dns.dmarc_p_none.title": {"pl": "Polityka DMARC to p=none", "en": "DMARC policy is p=none"},
    "flag.dns.dmarc_p_none.message": {
        "pl": "Raporty są zbierane, ale nic nie jest egzekwowane. **To normalna pozycja "
        "monitorująca** — problem tylko wtedy, gdy zamierzano egzekwować politykę.\n\n"
        "**Co zrobić:** jeśli Twoje SPF i DKIM od tygodni pokazują 95%+ zgodności w raportach, "
        "podnieś do `p=quarantine; pct=25` (podejrzana poczta ląduje w spamie u 25% odbiorców). "
        "Po tygodniu bez skarg → `pct=100`, potem `p=reject`. Jeśli zgodność jest niższa, zostaw "
        "`p=none` i najpierw napraw uwierzytelnianie.",
        "en": "Reports are collected but nothing is enforced. **This is the normal monitoring "
        "position** — only a problem if you intended to enforce.\n\n"
        "**What to do:** if your SPF and DKIM have been at 95%+ compliance in reports for weeks, "
        "step up to `p=quarantine; pct=25` (suspicious mail goes to spam for 25% of receivers). "
        "One week without complaints → `pct=100`, then `p=reject`. If compliance is lower, keep "
        "`p=none` and fix authentication first.",
    },
    "flag.dns.dmarc_pct.title": {
        "pl": "DMARC obejmuje tylko {pct}% poczty",
        "en": "DMARC applies to only {pct}% of mail",
    },
    "flag.dns.dmarc_pct.message": {
        "pl": "pct={pct} oznacza, że polityka jest stosowana do próbki. Wolumeny w raportach nie "
        "odzwierciedlą całej wysyłki.\n\n"
        "**Co zrobić:** to jest OK tylko podczas rollout DMARC (świadomie idziesz 25% → 50% → 100%). "
        "Jeśli utknąłeś tu na dłużej — podnieś do `pct=100`. Jeśli podnosisz i widzisz nagle "
        "problemy — obniż z powrotem i napraw uwierzytelnianie zanim znowu ruszysz w górę.",
        "en": "pct={pct} means the policy is applied to a sample. Report volumes will not reflect "
        "all of your sending.\n\n"
        "**What to do:** this is fine only during a DMARC rollout (deliberately going 25% → 50% "
        "→ 100%). If you've been stuck here — step up to `pct=100`. If stepping up surfaces new "
        "problems — step back down and fix authentication before trying again.",
    },
    "flag.dns.dkim_missing.title": {
        "pl": "Brak selektora DKIM: {selectors}",
        "en": "DKIM selector not found: {selectors}",
    },
    "flag.dns.dkim_missing.message": {
        "pl": "Nie opublikowano klucza DKIM dla {selector_word}. Jeśli domena podpisuje nim "
        "wiadomości, każda sygnatura nie przejdzie weryfikacji.\n\n"
        "**Co zrobić:** wejdź do panelu Twojej platformy wysyłkowej i sprawdź, jaki selektor "
        "faktycznie generuje (Google Workspace: Panel admina → Aplikacje → Google Workspace → "
        "Gmail → Uwierzytelnianie poczty; Microsoft 365: Defender → Zasady poczty → DKIM). Wpisz "
        "**dokładną** nazwę selektora do Ustawień. Jeśli platforma używa domyślnej nazwy jak `google`, "
        "`selector1` — a Ty jej nie masz w DNS — najprawdopodobniej DKIM nie jest w ogóle aktywny "
        "u dostawcy, trzeba go włączyć.",
        "en": "No DKIM key is published for {selector_word}. If the domain is signing with it, "
        "every signature will fail verification.\n\n"
        "**What to do:** go into your sending platform's panel and check which selector it "
        "actually generates (Google Workspace: Admin console → Apps → Google Workspace → Gmail → "
        "Authenticate email; Microsoft 365: Defender → Email policies → DKIM). Enter the **exact** "
        "selector name into Settings. If the platform uses a default name like `google` or "
        "`selector1` — and you don't have it in DNS — DKIM most likely isn't enabled at the "
        "provider at all, and you need to turn it on.",
    },
    "flag.dns.dkim_missing.selector_word_singular": {"pl": "tego selektora", "en": "this selector"},
    "flag.dns.dkim_missing.selector_word_plural": {"pl": "tych selektorów", "en": "these selectors"},
    "flag.dns.dmarc_no_policy_tag.title": {
        "pl": "DMARC bez znacznika polityki p=",
        "en": "DMARC record has no policy tag",
    },
    "flag.dns.dmarc_no_policy_tag.message": {
        "pl": "Rekord DMARC istnieje, ale nie ma znacznika 'p=', więc jest niekompletny i odbiorcy "
        "mogą go zignorować.",
        "en": "A DMARC record exists but has no 'p=' tag, so it is malformed and receivers may "
        "ignore it.",
    },
    "flag.dns.dkim_revoked.title": {"pl": "Klucz DKIM odwołany: {selectors}", "en": "DKIM key revoked: {selectors}"},
    "flag.dns.dkim_revoked.message": {
        "pl": "Selektor istnieje, ale publikuje pusty klucz (`p=`), co jawnie go **odwołuje**. "
        "Sygnatury nim wykonane nie przejdą weryfikacji.\n\n"
        "**Co zrobić:** dwie możliwości. **Po pierwsze** — jeśli to celowe (rotacja klucza), usuń "
        "ten selektor z Ustawień, żeby nie generował fałszywego alarmu. **Po drugie** — jeśli "
        "spodziewałeś się, że klucz działa, wejdź do panelu platformy wysyłkowej i wygeneruj nowy. "
        "To normalne, że stary selektor zostaje odwołany po rotacji (kiedy poczta go już nie "
        "podpisuje) — dopóki żadna wiadomość go nie używa, można spokojnie usunąć wpis z DNS.",
        "en": "The selector exists but publishes an empty key (`p=`), which explicitly **revokes** "
        "it. Signatures made with it will fail.\n\n"
        "**What to do:** two options. **First** — if this is deliberate (key rotation), remove "
        "the selector from Settings so it stops raising a false alarm. **Second** — if you "
        "expected the key to work, go into your sending platform's panel and generate a new one. "
        "It's normal for an old selector to be revoked after rotation (once no mail is signing "
        "with it) — as long as no message uses it, you can safely delete the DNS record.",
    },
    "flag.dns.no_mx.title": {"pl": "Brak rekordów MX", "en": "No MX records"},
    "flag.dns.no_mx.message": {
        "pl": "Ta domena w ogóle nie może odbierać poczty, co oznacza, że odbicia i odpowiedzi są "
        "tracone.\n\n"
        "**Co zrobić:** dodaj rekord MX wskazujący na Twojego dostawcę poczty. Dla Google "
        "Workspace: `1 SMTP.GOOGLE.COM`. Dla Microsoft 365: `1 <tenant>-com.mail.protection.outlook.com`. "
        "Bez MX narzędzie nie zobaczy żadnych odbić, a raporty rua też mogą przestać przychodzić "
        "(bo skrzynka rua nie istnieje).",
        "en": "This domain cannot receive mail at all, which means bounces and replies are being "
        "lost.\n\n"
        "**What to do:** add an MX record pointing to your mail provider. For Google Workspace: "
        "`1 SMTP.GOOGLE.COM`. For Microsoft 365: `1 <tenant>-com.mail.protection.outlook.com`. "
        "Without MX the tool won't see any bounces, and rua reports may stop arriving too (the "
        "rua mailbox has nowhere to live).",
    },

    # --- Flags: bounce ----------------------------------------------------------
    "flag.bounce.hard_rate_critical.title": {
        "pl": "Wskaźnik twardych odbić {rate:.1%}",
        "en": "Hard bounce rate {rate:.1%}",
    },
    "flag.bounce.hard_rate_critical.message": {
        "pl": "{hard} z ok. {sent} wiadomości odbiło się trwale. Powyżej {threshold:.0%} dostawcy "
        "zaczynają traktować nadawcę jako problem jakości listy, co szkodzi reputacji **każdej** "
        "domeny, z której wysyłasz — nie tylko tej.\n\n"
        "**Co zrobić:** wstrzymaj wysyłkę z tej domeny. Wejdź do logu odbić poniżej i wyeksportuj "
        "adresy z kodami `5.1.1`, `5.1.2`, `5.1.10` (nieistniejące adresy) — te usuń z listy "
        "kontaktów u źródła. Jeśli lista pochodzi z scrapowania, użyj weryfikatora "
        "(np. NeverBounce, ZeroBounce) przed następną kampanią. Wróć do wysyłki po tygodniu bez "
        "nowych twardych odbić.",
        "en": "{hard} of roughly {sent} messages bounced permanently. Above {threshold:.0%} "
        "providers start treating the sender as a list-quality problem, which damages reputation "
        "for **every** domain you send from — not just this one.\n\n"
        "**What to do:** pause sending from this domain. Go into the bounce log below and export "
        "addresses with codes `5.1.1`, `5.1.2`, `5.1.10` (non-existent addresses) — remove them "
        "from your source list. If the list comes from scraping, run it through a verifier "
        "(NeverBounce, ZeroBounce) before the next campaign. Resume sending after a week with no "
        "new hard bounces.",
    },
    "flag.bounce.hard_rate_warning.title": {
        "pl": "Wskaźnik twardych odbić {rate:.1%}",
        "en": "Hard bounce rate {rate:.1%}",
    },
    "flag.bounce.hard_rate_warning.message": {
        "pl": "{hard} z ok. {sent} wiadomości odbiło się trwale. To powyżej poziomu {threshold:.0%}, "
        "od którego dostawcy zaczynają zwracać uwagę. **Zwykle to nieaktualna lista**, nie "
        "zablokowana domena.\n\n"
        "**Co zrobić:** przejrzyj log odbić poniżej — kody `5.1.1`/`5.1.2` to nieistniejące "
        "adresy do usunięcia. Nie musisz jeszcze wstrzymywać wysyłki, ale jeśli w kolejnych 3–4 "
        "dniach wskaźnik rośnie zamiast spadać, potraktuj to tak jak poziom krytyczny.",
        "en": "{hard} of roughly {sent} messages bounced permanently. This is above the "
        "{threshold:.0%} level where providers begin to notice. **Usually a stale list**, not "
        "a blocked domain.\n\n"
        "**What to do:** review the bounce log below — `5.1.1`/`5.1.2` codes are non-existent "
        "addresses to remove. No need to pause yet, but if the rate keeps climbing over the next "
        "3–4 days, treat it like the critical level.",
    },
    "flag.bounce.unparsed.title": {
        "pl": "{count} odbić nie udało się przetworzyć",
        "en": "{count} bounce(s) could not be parsed",
    },
    "flag.bounce.unparsed.message": {
        "pl": "Te wiadomości wyglądały na odbicia, ale nie zawierały czytelnego kodu statusu. "
        "Ich pełna treść została zapisana.\n\n"
        "**Co zrobić:** w logu odbić poniżej te wpisy mają w kolumnie Rozpoznano wartość 'nie'. "
        "Zerknij, bo niestandardowe odbicia czasem są tym, jak dostawca zgłasza blokadę bez "
        "użycia standardowych kodów SMTP.",
        "en": "These messages looked like bounces but carried no readable status code. Their full "
        "text has been stored.\n\n"
        "**What to do:** in the bounce log below these entries show 'no' in the Parsed column. "
        "Take a look — non-standard bounces are sometimes how a provider reports a block without "
        "using standard SMTP codes.",
    },

    # --- Flags: DMARC compliance --------------------------------------------
    "flag.rua.low_compliance.title": {"pl": "Zgodność DMARC {rate:.1%}", "en": "DMARC compliance {rate:.1%}"},
    "flag.rua.low_compliance.message": {
        "pl": "{failed} z {evaluated} ocenionych wiadomości nie przeszło ani SPF, ani DKIM w "
        "dopasowaniu{worst_suffix}. Przekierowana poczta ({forwarded} wiadomości) jest już "
        "wyłączona, więc to prawdziwy błąd uwierzytelniania, nie przekierowanie.\n\n"
        "**Co zrobić:** spójrz na tabelę Źródła z największym wolumenem błędów niżej — pokaże "
        "konkretne adresy IP wysyłające jako Ta domena, ale bez ważnego SPF/DKIM. Trzy typowe "
        "przyczyny: **(1)** stary dostawca (np. Mailchimp) którego usunięto, ale konto dalej próbuje "
        "wysyłać — cofnij dostęp u dostawcy; **(2)** brakujący `include:` w SPF dla legalnego "
        "nowego dostawcy — dodaj; **(3)** ktoś podszywa się pod Twoją domenę — jeśli IP są spoza "
        "Twoich znanych dostawców, prawdopodobnie spam z sfałszowanym Twoim adresem.",
        "en": "{failed} of {evaluated} evaluated messages failed both SPF and DKIM alignment"
        "{worst_suffix}. Forwarded mail ({forwarded} messages) is already excluded, so this is "
        "genuine authentication failure rather than relaying.\n\n"
        "**What to do:** look at the 'Highest-volume failing sources' table below — it shows "
        "specific IPs sending as this domain without valid SPF/DKIM. Three common causes: "
        "**(1)** a former provider (e.g. Mailchimp) that was removed but the account is still "
        "trying — revoke the account at the provider; **(2)** a missing `include:` in SPF for a "
        "legitimate new provider — add it; **(3)** someone spoofing your domain — if the IPs "
        "aren't from any of your known providers, likely spam forging your address.",
    },
    "flag.rua.low_compliance.worst_suffix": {
        "pl": ", głównie widziane przez {esp}",
        "en": ", mostly as seen by {esp}",
    },
    "flag.rua.no_data.title": {"pl": "Brak danych z raportów DMARC", "en": "No DMARC report data"},
    "flag.rua.no_data.message": {
        "pl": "W wybranym zakresie nie napłynęły żadne raporty zbiorcze dla tej domeny.\n\n"
        "**Co zrobić:** sprawdź trzy rzeczy. **(1)** Czy z tej domeny w ogóle coś wysyłasz — jeśli "
        "nie, flaga jest bezpodstawna, można ją zignorować. **(2)** Czy domena ma rekord DMARC "
        "z `rua=` (patrz sekcja Rekordy DNS niżej). **(3)** Czy skrzynka wskazana w `rua=` jest "
        "dodana w Ustawieniach i pobiera pocztę bez błędów. Pamiętaj że raporty rua przychodzą "
        "z opóźnieniem 24–48 h — widok 1-dniowy prawie zawsze będzie pusty.",
        "en": "No aggregate reports have arrived for this domain in the selected window.\n\n"
        "**What to do:** check three things. **(1)** Do you actually send from this domain — if "
        "not, the flag is spurious, ignore it. **(2)** Does the domain have a DMARC record with "
        "`rua=` (see DNS records below). **(3)** Is the mailbox in `rua=` added in Settings and "
        "fetching without errors. Remember rua reports arrive with 24–48 h latency — the 1-day "
        "view will almost always be empty.",
    },

    # --- Headline (one-sentence answer) --------------------------------------
    "headline.no_data": {"pl": "Brak jeszcze danych dla tej domeny.", "en": "No data yet — nothing to report for this domain."},
    "headline.ok_with_rate": {
        "pl": "Nie wykryto problemów. Zgodność DMARC {rate:.1%}.",
        "en": "No problems detected. DMARC compliance {rate:.1%}.",
    },
    "headline.ok_no_rate": {"pl": "Nie wykryto problemów.", "en": "No problems detected."},
    "headline.critical_more": {"pl": " (+{n} więcej krytycznych)", "en": " (+{n} more critical)"},
    "headline.warning_more": {"pl": " (+{n} więcej)", "en": " (+{n} more)"},

    # --- SPF/DMARC validator detail (composed into flag.dns.spf_error.*) -----
    "dns.error.include_loop": {
        "pl": "Wykryto pętlę include na {domain!r} — ocena SPF zakończyłaby się błędem.",
        "en": "Include loop detected at {domain!r} — SPF evaluation would fail.",
    },
    "dns.error.nesting_too_deep": {
        "pl": "Zagnieżdżenie include głębsze niż 10 poziomów; zatrzymano zagłębianie.",
        "en": "Include nesting deeper than 10 levels; stopped descending.",
    },
    "dns.error.include_missing": {
        "pl": "include:{domain} nie ma rekordu SPF — to permerror u odbiorcy.",
        "en": "include:{domain} has no SPF record — this is a permerror at the receiver.",
    },
    "dns.error.multiple_records": {
        "pl": "Opublikowano więcej niż jeden rekord SPF; odbiorcy traktują to jako permerror.",
        "en": "More than one SPF record published; receivers treat this as a permerror.",
    },
    "dns.error.no_all": {
        "pl": "Rekord SPF nie ma mechanizmu 'all', więc nie określa, co zrobić z nadawcami spoza "
        "listy.",
        "en": "SPF record has no 'all' mechanism, so it does not say what to do with unlisted "
        "senders.",
    },
    "dns.error.plus_all": {
        "pl": "SPF kończy się na '+all', co upoważnia cały internet do wysyłania jako ta domena.",
        "en": "SPF ends in '+all', which authorises the entire internet to send as this domain.",
    },
    "dns.error.resolver": {"pl": "Zapytanie DNS nie powiodło się: {error}", "en": "DNS lookup failed: {error}"},

    # --- Settings screen ------------------------------------------------------
    "settings.title": {"pl": "Ustawienia", "en": "Settings"},
    "settings.subtitle": {
        "pl": "Domeny i skrzynki, z których narzędzie zbiera dane.",
        "en": "The domains and mailboxes this tool collects data from.",
    },
    "settings.domains": {"pl": "Monitorowane domeny", "en": "Monitored domains"},
    "settings.domains_hint": {
        "pl": "selektory DKIM trzeba podać ręcznie — nie da się ich odczytać z DNS",
        "en": "DKIM selectors must be entered by hand — they cannot be read from DNS",
    },
    "settings.mailboxes_rua": {"pl": "Skrzynki z raportami DMARC", "en": "DMARC report mailboxes"},
    "settings.mailboxes_rua_hint": {
        "pl": "tu przychodzą raporty rua; może być kilka skrzynek",
        "en": "where rua reports arrive; there can be several",
    },
    "settings.mailboxes_bounce": {"pl": "Skrzynki nadawcze (odbicia)", "en": "Sending mailboxes (bounces)"},
    "settings.mailboxes_bounce_hint": {
        "pl": "NDR wraca zawsze na skrzynkę, z której wyszedł mail — jedna pozycja na skrzynkę",
        "en": "an NDR always returns to the mailbox that sent the message — one entry per mailbox",
    },
    "settings.add_domain": {"pl": "Dodaj domenę", "en": "Add domain"},
    "settings.add_mailbox": {"pl": "Dodaj skrzynkę", "en": "Add mailbox"},
    "settings.no_domains": {"pl": "Nie dodano jeszcze żadnej domeny.", "en": "No domains added yet."},
    "settings.no_mailboxes": {"pl": "Nie dodano jeszcze żadnej skrzynki.", "en": "No mailboxes added yet."},

    "field.domain": {"pl": "Domena", "en": "Domain"},
    "field.dkim_selectors": {"pl": "Selektory DKIM", "en": "DKIM selectors"},
    "field.dkim_hint": {"pl": "po przecinku, np. google, selector1", "en": "comma separated, e.g. google, selector1"},
    "field.notes": {"pl": "Notatki", "en": "Notes"},
    "field.name": {"pl": "Nazwa", "en": "Name"},
    "field.name_hint": {"pl": "dowolna etykieta, np. rua-glowna", "en": "any label, e.g. rua-main"},
    "field.host": {"pl": "Serwer IMAP", "en": "IMAP host"},
    "field.port": {"pl": "Port", "en": "Port"},
    "field.ssl": {"pl": "SSL/TLS", "en": "SSL/TLS"},
    "field.username": {"pl": "Użytkownik", "en": "Username"},
    "field.password": {"pl": "Hasło", "en": "Password"},
    "field.password_hint_new": {
        "pl": "zapisywane zaszyfrowane; użyj hasła aplikacji, nie głównego hasła do konta",
        "en": "stored encrypted; use an app password, not your main account password",
    },
    "field.password_hint_edit": {
        "pl": "zostaw puste, żeby nie zmieniać zapisanego hasła",
        "en": "leave blank to keep the saved password",
    },
    "field.folder": {"pl": "Folder", "en": "Folder"},
    "field.processed_folder": {"pl": "Folder na przetworzone", "en": "Processed folder"},
    "field.processed_hint": {"pl": "opcjonalnie", "en": "optional"},
    "field.sending_domain": {"pl": "Domena nadawcza", "en": "Sending domain"},
    "field.enabled": {"pl": "Aktywna", "en": "Enabled"},

    "btn.save": {"pl": "Zapisz", "en": "Save"},
    "btn.cancel": {"pl": "Anuluj", "en": "Cancel"},
    "btn.edit": {"pl": "Edytuj", "en": "Edit"},
    "btn.delete": {"pl": "Usuń", "en": "Delete"},
    "btn.test": {"pl": "Testuj połączenie", "en": "Test connection"},
    "btn.test_domain": {"pl": "Sprawdź DNS", "en": "Check DNS"},
    "btn.testing": {"pl": "Sprawdzam…", "en": "Testing…"},
    "btn.close": {"pl": "Zamknij", "en": "Close"},

    "confirm.delete_domain": {
        "pl": "Usunąć tę domenę? Wszystkie zebrane dane (raporty DMARC, odbicia, wyniki DNS, blacklisty) zostaną trwale skasowane.",
        "en": "Delete this domain? All collected data (DMARC reports, bounces, DNS results, blacklist checks) will be permanently deleted.",
    },
    "confirm.delete_mailbox": {
        "pl": "Usunąć tę skrzynkę? Zapisane hasło zostanie skasowane.",
        "en": "Remove this mailbox? The stored password will be deleted.",
    },

    "status.disabled": {"pl": "wyłączona", "en": "disabled"},
    "status.never_tested": {"pl": "nietestowana", "en": "never tested"},
    "status.test_ok": {"pl": "połączenie OK", "en": "connection OK"},
    "status.test_failed": {"pl": "błąd połączenia", "en": "connection failed"},
    "status.no_password": {"pl": "brak hasła", "en": "no password"},
    "status.password_from_env": {"pl": "hasło z .env", "en": "password from .env"},

    # --- Connection / domain test results -------------------------------------
    "test.ok": {
        "pl": "Połączono. Folder {folder} zawiera {count} wiadomości.",
        "en": "Connected. Folder {folder} holds {count} messages.",
    },
    "test.login_failed": {
        "pl": "Serwer odrzucił login lub hasło. Jeśli konto ma dwuskładnikowe logowanie, "
        "potrzebne jest hasło aplikacji, nie zwykłe hasło. ({error})",
        "en": "The server rejected the username or password. If the account has two-factor "
        "authentication, you need an app password rather than the account password. ({error})",
    },
    "test.host_unknown": {
        "pl": "Nie udało się rozwiązać adresu {host}. Sprawdź, czy nazwa serwera jest poprawna.",
        "en": "Could not resolve {host}. Check the server name.",
    },
    "test.timeout": {
        "pl": "Przekroczono czas oczekiwania na {host}:{port}. Serwer nie odpowiada albo port jest zablokowany.",
        "en": "Timed out connecting to {host}:{port}. The server is not responding or the port is blocked.",
    },
    "test.ssl_error": {
        "pl": "Błąd SSL/TLS: {error}. Sprawdź, czy port i ustawienie SSL do siebie pasują "
        "(zwykle 993 z SSL, 143 bez).",
        "en": "SSL/TLS error: {error}. Check that the port and the SSL setting match "
        "(usually 993 with SSL, 143 without).",
    },
    "test.connection_failed": {
        "pl": "Nie udało się połączyć z {host}:{port} ({error}).",
        "en": "Could not connect to {host}:{port} ({error}).",
    },
    "test.imap_error": {"pl": "Serwer IMAP zwrócił błąd: {error}", "en": "The IMAP server returned an error: {error}"},
    "test.unexpected": {"pl": "Nieoczekiwany błąd: {error}", "en": "Unexpected error: {error}"},
    "test.fields_missing": {
        "pl": "Najpierw wypełnij wymagane pola, potem uruchom test.",
        "en": "Fill in the required fields first, then run the test.",
    },
    "test.http_error": {
        "pl": "Serwer odpowiedział błędem ({status}). Sprawdź logi aplikacji.",
        "en": "The server returned an error ({status}). Check the application logs.",
    },
    "test.no_password": {
        "pl": "Brak hasła do przetestowania. Wpisz je w formularzu. {error}",
        "en": "No password to test with. Enter one in the form. {error}",
    },
    "test.folder_missing": {
        "pl": "Zalogowano, ale folder {folder} nie istnieje. Dostępne: {available}",
        "en": "Logged in, but folder {folder} does not exist. Available: {available}",
    },
    "test.domain_ok": {
        "pl": "{domain} wygląda poprawnie — SPF, DMARC z adresem rua i DKIM ({selectors}) są na miejscu.",
        "en": "{domain} looks correct — SPF, DMARC with a rua address, and DKIM ({selectors}) are all present.",
    },
    "test.domain_gaps": {
        "pl": "{domain} istnieje, ale brakuje: {gaps}. Można ją dodać — narzędzie właśnie po to jest, "
        "żeby takie braki pokazywać.",
        "en": "{domain} exists but is missing: {gaps}. You can still add it — surfacing gaps like "
        "these is what the tool is for.",
    },
    "test.domain_nxdomain": {
        "pl": "Domena {domain} nie istnieje w DNS. Sprawdź pisownię.",
        "en": "Domain {domain} does not exist in DNS. Check the spelling.",
    },
    "test.domain_malformed": {
        "pl": "{domain} nie wygląda na poprawną nazwę domeny.",
        "en": "{domain} does not look like a valid domain name.",
    },
    "test.domain_dns_error": {
        "pl": "Nie udało się sprawdzić {domain}: {error}",
        "en": "Could not check {domain}: {error}",
    },

    # --- Saving / errors ------------------------------------------------------
    # Redirect-carried notices name no specific record — the redirect only
    # carries a short code, and the saved row is already visible in the list.
    "saved.domain": {"pl": "Zapisano domenę.", "en": "Domain saved."},
    "saved.mailbox": {"pl": "Zapisano skrzynkę.", "en": "Mailbox saved."},
    "deleted.domain": {"pl": "Usunięto domenę.", "en": "Domain removed."},
    "deleted.mailbox": {"pl": "Usunięto skrzynkę.", "en": "Mailbox removed."},
    "error.domain_exists": {
        "pl": "Ta domena jest już na liście.",
        "en": "That domain is already on the list.",
    },
    "error.mailbox_exists": {
        "pl": "Skrzynka o tej nazwie już istnieje. Użyj innej nazwy.",
        "en": "A mailbox with that name already exists. Use a different name.",
    },
    "error.required": {"pl": "Wypełnij wymagane pola.", "en": "Fill in the required fields."},
    "error.no_secret_key": {
        "pl": "Nie ustawiono SECRET_KEY w .env, więc nie da się zapisać hasła. "
        "Wygeneruj klucz: python -m deliverability.cli genkey",
        "en": "SECRET_KEY is not set in .env, so passwords cannot be stored. "
        "Generate one with: python -m deliverability.cli genkey",
    },

    # --- Run now --------------------------------------------------------------
    "action.refresh": {"pl": "Sprawdź teraz", "en": "Check now"},
    "action.refresh_all": {"pl": "Sprawdź wszystko", "en": "Check everything"},
    "action.running": {"pl": "W trakcie…", "en": "Running…"},
    "action.started": {"pl": "Uruchomiono sprawdzanie ({stream}).", "en": "Started {stream} check."},
    "action.already_running": {
        "pl": "Sprawdzanie {stream} już trwa.",
        "en": "A {stream} check is already running.",
    },

    # --- Bounce log table (domain page) ---------------------------------------
    "section.bounce_log": {"pl": "Log odbić", "en": "Bounce log"},
    "section.bounce_log_hint": {
        "pl": "surowe wpisy do weryfikacji ręcznej w skrzynce",
        "en": "raw entries, for manual cross-checking against the mailbox",
    },
    "table.received_at": {"pl": "Otrzymano", "en": "Received"},
    "table.mailbox": {"pl": "Skrzynka", "en": "Mailbox"},
    "table.recipient_domain": {"pl": "Domena odbiorcy", "en": "Recipient domain"},
    "table.parsed": {"pl": "Rozpoznano", "en": "Parsed"},
    "table.yes": {"pl": "tak", "en": "yes"},
    "table.no": {"pl": "nie", "en": "no"},

    # --- One-line, human help for individual bounce codes ---------------------
    # Rendered inline under each bounce diagnostic in the log, so the user can
    # tell at a glance what the SMTP jargon means without having to look it up.
    # Only common codes here; anything else falls back to no extra help line,
    # which is fine — the raw diagnostic is still visible next to it.
    "bounce.code_help.5.1.1": {
        "pl": "Adres odbiorcy nie istnieje — usuń z listy.",
        "en": "Recipient address does not exist — remove from list.",
    },
    "bounce.code_help.5.1.2": {
        "pl": "Domena odbiorcy nie istnieje lub nie ma serwera poczty — literówka w domenie?",
        "en": "Recipient domain does not exist or has no mail server — typo in the domain?",
    },
    "bounce.code_help.5.1.3": {
        "pl": "Adres źle skonstruowany.",
        "en": "Malformed recipient address.",
    },
    "bounce.code_help.5.1.6": {"pl": "Skrzynka przeniesiona, brak forwardowania.", "en": "Mailbox moved, no forwarding."},
    "bounce.code_help.5.1.10": {"pl": "Adres nierozwiązywalny — usuń.", "en": "Address does not resolve — remove."},
    "bounce.code_help.5.2.1": {"pl": "Skrzynka wyłączona.", "en": "Mailbox is disabled."},
    "bounce.code_help.5.2.2": {"pl": "Skrzynka pełna (mimo kodu 5.x.x zwykle temporary).", "en": "Mailbox full (usually temporary despite 5.x.x)."},
    "bounce.code_help.5.4.4": {
        "pl": "Serwer odbiorcy nie odpowiada — problem po ich stronie.",
        "en": "Recipient server not reachable — their side.",
    },
    "bounce.code_help.5.7.0": {
        "pl": "Odrzucone przez politykę — najczęściej reputacja lub treść. Sprawdź pełną treść diagnostyki.",
        "en": "Rejected by policy — usually reputation or content. Check the full diagnostic.",
    },
    "bounce.code_help.5.7.1": {
        "pl": "Odrzucone przez politykę — może być blokada reputacji, ale też loop, whitelist, "
        "relay denied. Zawsze sprawdź treść diagnostyki obok.",
        "en": "Rejected by policy — could be a reputation block, but also a loop, whitelist, or "
        "'relay denied'. Always check the diagnostic next to it.",
    },
    "bounce.code_help.5.7.708": {
        "pl": "Microsoft: cały zakres IP zablokowany za reputację.",
        "en": "Microsoft: whole IP range blocked on reputation.",
    },
    "bounce.code_help.5.7.509": {"pl": "Nieprzeszły DMARC u odbiorcy.", "en": "DMARC failed at receiver."},
    "bounce.code_help.5.7.23": {"pl": "Nieprzeszły SPF u odbiorcy.", "en": "SPF failed at receiver."},
    "bounce.code_help.5.7.26": {"pl": "Nieprzeszły SPF **i** DKIM — napraw uwierzytelnianie.", "en": "SPF **and** DKIM failed — fix authentication."},
    "bounce.code_help.5.0.0": {"pl": "Ogólny błąd trwały — patrz diagnostyka.", "en": "Generic permanent error — see diagnostic."},
    "bounce.code_help.4.2.2": {"pl": "Skrzynka pełna — może się zwolnić.", "en": "Mailbox full — may clear on its own."},
    "bounce.code_help.4.3.2": {"pl": "Serwer odbiorcy chwilowo nie przyjmuje.", "en": "Recipient server temporarily not accepting."},
    "bounce.code_help.4.4.1": {
        "pl": "Serwer odbiorcy nie odpowiada — chwilowy problem po ich stronie.",
        "en": "Recipient server not responding — temporary, their side.",
    },
    "bounce.code_help.4.4.2": {"pl": "Połączenie zerwane w trakcie doręczania.", "en": "Connection dropped mid-delivery."},
    "bounce.code_help.4.4.4": {
        "pl": "Odbiorca dostał wiadomość jako nieuwierzytelnioną — u nich brakuje konfiguracji dla ich tenanta.",
        "en": "Recipient got the message as unauthenticated — their tenant is misconfigured.",
    },
    "bounce.code_help.4.7.1": {
        "pl": "Chwilowo odrzucone przez politykę — greylisting lub limit. Uważaj, powtórzenia często "
        "przechodzą w 5.7.x.",
        "en": "Temporarily refused by policy — greylisting or a rate limit. Watch out, repeats "
        "often turn into 5.7.x.",
    },
    "bounce.code_help.4.7.0": {
        "pl": "Chwilowo odroczone na tle reputacji.",
        "en": "Temporarily deferred on reputation grounds.",
    },

    # --- Domain consistency between the three places -------------------------
    "field.sending_domain_hint": {
        "pl": "wybierz z listy monitorowanych domen — dane odbić przypiszą się właśnie do niej",
        "en": "pick from monitored domains — bounce data will be tied to this one",
    },
    "field.sending_domain_placeholder": {
        "pl": "— wybierz domenę —",
        "en": "— pick a domain —",
    },
    "field.sending_domain_orphan_suffix": {
        "pl": "(nie ma jej już na liście monitorowanych)",
        "en": "(no longer on the monitored list)",
    },
    "error.no_domains_for_bounce": {
        # Straight quotes only in these strings — the file is Python source, and
        # a stray “curly” inside a "double-quoted" template closes the string.
        "pl": "Najpierw dodaj domenę w sekcji Monitorowane domeny. Bez tego skrzynka odbić nie ma do czego się podpiąć.",
        "en": "Add a domain under Monitored domains first. A bounce mailbox needs a monitored domain to attach to.",
    },
    "error.bounce_domain_unknown": {
        "pl": "Domena {domain} nie jest na liście monitorowanych. Dodaj ją tam albo wybierz inną.",
        "en": "Domain {domain} is not in the monitored list. Add it there, or pick a different one.",
    },
    "warning.bounce_username_mismatch": {
        "pl": "Uwaga: użytkownik skrzynki jest w domenie {user_domain}, a jako domena nadawcza wybrano {picked}. To bywa "
        "poprawne (np. alias), ale częściej to literówka — sprawdź, że dane odbić trafią tam, gdzie chcesz.",
        "en": "Warning: the mailbox user is at {user_domain} but the sending domain is set to {picked}. This can be "
        "correct (aliases), but is more often a typo — check that bounce data will land where you expect.",
    },

    "section.unknown_domains": {"pl": "Nieznane domeny w raportach", "en": "Unknown domains in reports"},
    "section.unknown_domains_hint": {
        "pl": "raporty rua przychodzą dla tych domen, ale ich nie ma na liście monitorowanych — literówka albo brakująca pozycja",
        "en": "rua reports are arriving for these domains, but they are not on the monitored list — a typo or a missing entry",
    },
    "unknown.report_count": {"pl": "{n} raport(y)", "en": "{n} report(s)"},
    "unknown.latest": {"pl": "ostatni {when}", "en": "latest {when}"},
    "unknown.add": {"pl": "Dodaj do monitorowanych", "en": "Add to monitored"},
    "unknown.none": {
        "pl": "Wszystkie raporty rua trafiają w domeny z Twojej listy. Nic do posprzątania.",
        "en": "Every rua report matches a monitored domain. Nothing to reconcile.",
    },

    # --- Combined add-domain-plus-bounce-mailbox flow -------------------------
    "settings.add_bounce_now": {
        "pl": "Dodaj też skrzynkę bounce dla tej domeny",
        "en": "Also add a bounce mailbox for this domain",
    },
    "settings.add_rua_now": {
        "pl": "Dodaj też skrzynkę rua (raporty DMARC) dla tej domeny",
        "en": "Also add a rua mailbox (DMARC reports) for this domain",
    },
    "field.rua_name_hint": {
        "pl": "puste = nazwa domeny z dopiskiem -rua",
        "en": "blank = the domain name with a -rua suffix",
    },
    "field.bounce_name_hint": {
        "pl": "puste = użyta zostanie nazwa domeny",
        "en": "blank = the domain name is used",
    },

    # --- One-time nudge to add a central rua mailbox --------------------------
    "settings.no_rua_nudge_title": {
        "pl": "Nie masz jeszcze skrzynki na raporty DMARC",
        "en": "You don't have a DMARC report mailbox yet",
    },
    "settings.no_rua_nudge_body": {
        "pl": "Jedna centralna skrzynka wystarczy dla wszystkich domen naraz — nie trzeba jej dodawać osobno dla każdej.",
        "en": "One central mailbox is enough for every domain at once — no need to add it per domain.",
    },
    "settings.no_rua_nudge_cta": {"pl": "Dodaj skrzynkę rua", "en": "Add rua mailbox"},
}


def translate(key: str, lang: str, **params: Any) -> str:
    """Resolve a message key to text in the given language.

    Falls back to English if the language or key is missing, and to the bare
    key if neither exists — visible-but-safe, so a missing translation shows up
    as an odd label instead of a crash. Any :class:`Nested` param is resolved
    first, one level deep, so a template can embed a sub-choice that is itself
    language-dependent (e.g. singular/plural wording).
    """
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    template = entry.get(lang) or entry.get("en") or entry.get(DEFAULT_LANG) or key
    if not params:
        return template

    resolved = {
        name: (translate(value["key"], lang, **value["params"]) if value["key"] else "")
        if _is_nested(value)
        else value
        for name, value in params.items()
    }
    try:
        return template.format(**resolved)
    except (KeyError, IndexError, ValueError):
        return template


def resolve_lang(requested: Any) -> str:
    """Normalise an arbitrary requested language to a supported one."""
    value = (requested or "").strip().lower()[:2]
    return value if value in SUPPORTED_LANGS else DEFAULT_LANG
