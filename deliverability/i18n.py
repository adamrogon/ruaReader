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
    "section.dns_records": {"pl": "Rekordy DNS", "en": "DNS records"},
    "section.dns_checked_at": {"pl": "sprawdzono {when}", "en": "checked {when}"},
    "section.failing_sources": {
        "pl": "Źródła z największym wolumenem błędów",
        "en": "Highest-volume failing sources",
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
        "pl": "{esp} odrzuca pocztę z tej domeny{codes_suffix}. To odrzucenie samego nadawcy, nie "
        "pojedynczych adresów, więc dotyczy wszystkiego wysyłanego z tej domeny. Wstrzymaj wysyłkę "
        "z tej domeny do wyjaśnienia sprawy i sprawdź, czy ten sam kod pojawia się u innych dostawców.",
        "en": "{esp} is refusing mail from this domain{codes_suffix}. This is a rejection of the "
        "sender, not of individual addresses, so it affects everything sent from here. Stop "
        "sending from this domain until it is resolved, and check whether the same code is "
        "appearing at other providers.",
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
        "trafiania do spamu u użytkowników tego dostawcy. Zgłoś usunięcie z listy u operatora i "
        "znajdź przyczynę, zanim wyślesz więcej.",
        "en": "{names} is a list receivers act on directly — expect mail to this provider's users "
        "to be rejected or junked. Request delisting with the operator and find what triggered it "
        "before sending more.",
    },
    "flag.blacklist.detail_netblock_wide": {
        "pl": "Wszystkie te wpisy pochodzą z {names}, która listuje całe pule adresów/ASN, nie "
        "pojedynczych nadawców. Zwykle odzwierciedla to sąsiadów u dostawcy hostingu, nie Twoją "
        "wysyłkę, i większość dużych odbiorców tego nie uwzględnia. Warto zweryfikować na liście "
        "typu Spamhaus, zanim uznasz to za przyczynę problemów z dostarczalnością.",
        "en": "All of these listings are from {names}, which list whole netblocks or ASNs rather "
        "than individual senders. That usually reflects the hosting provider's neighbours rather "
        "than your own sending, and most large receivers do not act on them. Worth confirming "
        "against a list like Spamhaus before treating it as the cause of a delivery problem.",
    },
    "flag.blacklist.detail_mixed": {
        "pl": "Odbiorcy sprawdzający te listy mogą odrzucać lub filtrować pocztę z tego IP. Sprawdź, "
        "o którą listę chodzi, zanim zaczniesz działać — mocno różnią się popularnością.",
        "en": "Receivers consulting these lists may reject or junk mail from this IP. Check which "
        "list is involved before acting — they vary a lot in how widely they are used.",
    },

    # --- Flags: DNS ------------------------------------------------------------
    "flag.dns.no_spf.title": {"pl": "Brak rekordu SPF", "en": "No SPF record"},
    "flag.dns.no_spf.message": {
        "pl": "Ta domena nie publikuje rekordu SPF, więc odbiorcy nie mają listy autoryzowanych "
        "nadawców. Poczta z niej dużo częściej trafia do filtrów.",
        "en": "This domain publishes no SPF record, so receivers have no list of authorised "
        "senders. Mail from it is far more likely to be filtered.",
    },
    "flag.dns.spf_over_limit.title": {
        "pl": "SPF przekracza limit zapytań DNS ({count}/{limit})",
        "en": "SPF exceeds the DNS lookup limit ({count}/{limit})",
    },
    "flag.dns.spf_over_limit.message": {
        "pl": "Ocena SPF wymaga {count} zapytań DNS, a limit z RFC to {limit}. Odbiorcy zwracają "
        "permerror i traktują SPF jako niezaliczone, niezależnie od tego, jak poprawna jest reszta "
        "rekordu. Usuń lub spłaszcz jeden z include.",
        "en": "SPF evaluation needs {count} DNS lookups but the RFC limit is {limit}. Receivers "
        "return permerror and treat SPF as failed, however correct the rest of the record is. "
        "Remove or flatten an include.",
    },
    "flag.dns.spf_near_limit.title": {
        "pl": "SPF blisko limitu zapytań ({count}/{limit})",
        "en": "SPF close to the lookup limit ({count}/{limit})",
    },
    "flag.dns.spf_near_limit.message": {
        "pl": "SPF zużywa obecnie {count} z {limit} dozwolonych zapytań DNS. Dodanie kolejnej usługi "
        "wysyłkowej prawdopodobnie to zepsuje.",
        "en": "SPF currently costs {count} of the {limit} permitted DNS lookups. Adding one more "
        "sending service will likely break it.",
    },
    "flag.dns.spf_error.title": {"pl": "Problem z rekordem SPF", "en": "SPF record problem"},
    "flag.dns.no_dmarc.title": {"pl": "Brak rekordu DMARC", "en": "No DMARC record"},
    "flag.dns.no_dmarc.message": {
        "pl": "Bez rekordu DMARC nie powstają żadne raporty zbiorcze, więc ta domena jest niewidoczna "
        "dla reszty tego narzędzia.",
        "en": "Without a DMARC record no aggregate reports are produced, so this domain is "
        "invisible to the rest of this tool.",
    },
    "flag.dns.dmarc_no_rua.title": {"pl": "DMARC bez adresu rua=", "en": "DMARC has no rua= address"},
    "flag.dns.dmarc_no_rua.message": {
        "pl": "Rekord DMARC nie prosi o raporty zbiorcze, więc żadne dane nigdy nie napłyną dla tej "
        "domeny.",
        "en": "The DMARC record does not ask for aggregate reports, so no data will ever arrive "
        "for this domain.",
    },
    "flag.dns.dmarc_p_none.title": {"pl": "Polityka DMARC to p=none", "en": "DMARC policy is p=none"},
    "flag.dns.dmarc_p_none.message": {
        "pl": "Raporty są zbierane, ale nic nie jest egzekwowane. To normalna pozycja monitorująca — "
        "problem tylko wtedy, gdy zamierzano egzekwować politykę.",
        "en": "Reports are collected but nothing is enforced. This is the normal monitoring "
        "position — it is only a problem if you intended to enforce.",
    },
    "flag.dns.dmarc_pct.title": {
        "pl": "DMARC obejmuje tylko {pct}% poczty",
        "en": "DMARC applies to only {pct}% of mail",
    },
    "flag.dns.dmarc_pct.message": {
        "pl": "pct={pct} oznacza, że polityka jest stosowana do próbki. Wolumeny w raportach nie "
        "odzwierciedlą całej wysyłki.",
        "en": "pct={pct} means the policy is applied to a sample. Report volumes will not reflect "
        "all of your sending.",
    },
    "flag.dns.dkim_missing.title": {
        "pl": "Brak selektora DKIM: {selectors}",
        "en": "DKIM selector not found: {selectors}",
    },
    "flag.dns.dkim_missing.message": {
        "pl": "Nie opublikowano klucza DKIM dla {selector_word}. Jeśli domena podpisuje nim "
        "wiadomości, każda sygnatura nie przejdzie weryfikacji. Sprawdź nazwy selektorów w "
        "config/domains.yml wobec Twojej platformy wysyłkowej.",
        "en": "No DKIM key is published for {selector_word}. If the domain is signing with it, "
        "every signature will fail verification. Check the selector names in config/domains.yml "
        "against your sending platform.",
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
        "pl": "Selektor istnieje, ale publikuje pusty klucz, co jawnie go odwołuje. Sygnatury nim "
        "wykonane nie przejdą weryfikacji.",
        "en": "The selector exists but publishes an empty key, which explicitly revokes it. "
        "Signatures made with it will fail.",
    },
    "flag.dns.no_mx.title": {"pl": "Brak rekordów MX", "en": "No MX records"},
    "flag.dns.no_mx.message": {
        "pl": "Ta domena w ogóle nie może odbierać poczty, co oznacza, że odbicia i odpowiedzi są "
        "tracone.",
        "en": "This domain cannot receive mail at all, which means bounces and replies are being "
        "lost.",
    },

    # --- Flags: bounce ----------------------------------------------------------
    "flag.bounce.hard_rate_critical.title": {
        "pl": "Wskaźnik twardych odbić {rate:.1%}",
        "en": "Hard bounce rate {rate:.1%}",
    },
    "flag.bounce.hard_rate_critical.message": {
        "pl": "{hard} z ok. {sent} wiadomości odbiło się trwale. Powyżej {threshold:.0%} dostawcy "
        "zaczynają traktować nadawcę jako problem jakości listy, co szkodzi reputacji każdej domeny, "
        "z której wysyłasz. Wyczyść listę przed kontynuowaniem.",
        "en": "{hard} of roughly {sent} messages bounced permanently. Above {threshold:.0%} "
        "providers start treating the sender as a list-quality problem, which damages reputation "
        "for every domain you send from. Clean the list before continuing.",
    },
    "flag.bounce.hard_rate_warning.title": {
        "pl": "Wskaźnik twardych odbić {rate:.1%}",
        "en": "Hard bounce rate {rate:.1%}",
    },
    "flag.bounce.hard_rate_warning.message": {
        "pl": "{hard} z ok. {sent} wiadomości odbiło się trwale. To powyżej poziomu {threshold:.0%}, "
        "od którego dostawcy zaczynają zwracać uwagę. Zwykle to nieaktualna lista, nie zablokowana "
        "domena.",
        "en": "{hard} of roughly {sent} messages bounced permanently. This is above the "
        "{threshold:.0%} level where providers begin to notice. Usually a stale list rather than "
        "a blocked domain.",
    },
    "flag.bounce.unparsed.title": {
        "pl": "{count} odbić nie udało się przetworzyć",
        "en": "{count} bounce(s) could not be parsed",
    },
    "flag.bounce.unparsed.message": {
        "pl": "Te wiadomości wyglądały na odbicia, ale nie zawierały czytelnego kodu statusu. Ich "
        "pełna treść została zapisana — warto zerknąć, bo niestandardowe odbicia czasem są tym, jak "
        "dostawca zgłasza blokadę.",
        "en": "These messages looked like bounces but carried no readable status code. Their full "
        "text has been stored — worth a look, since non-standard bounces are sometimes how a "
        "provider reports a block.",
    },

    # --- Flags: DMARC compliance --------------------------------------------
    "flag.rua.low_compliance.title": {"pl": "Zgodność DMARC {rate:.1%}", "en": "DMARC compliance {rate:.1%}"},
    "flag.rua.low_compliance.message": {
        "pl": "{failed} z {evaluated} ocenionych wiadomości nie przeszło ani SPF, ani DKIM w "
        "dopasowaniu{worst_suffix}. Przekierowana poczta ({forwarded} wiadomości) jest już wyłączona, "
        "więc to prawdziwy błąd uwierzytelniania, nie przekierowanie.",
        "en": "{failed} of {evaluated} evaluated messages failed both SPF and DKIM alignment"
        "{worst_suffix}. Forwarded mail ({forwarded} messages) is already excluded, so this is "
        "genuine authentication failure rather than relaying.",
    },
    "flag.rua.low_compliance.worst_suffix": {
        "pl": ", głównie widziane przez {esp}",
        "en": ", mostly as seen by {esp}",
    },
    "flag.rua.no_data.title": {"pl": "Brak danych z raportów DMARC", "en": "No DMARC report data"},
    "flag.rua.no_data.message": {
        "pl": "W wybranym zakresie nie napłynęły żadne raporty zbiorcze dla tej domeny. Albo nic z "
        "niej nie wysyłasz, albo jej adres rua= nie wskazuje na skrzynkę odczytywaną przez to "
        "narzędzie.",
        "en": "No aggregate reports have arrived for this domain in the selected window. Either it "
        "is not sending, or its DMARC rua= address is not pointing at a mailbox this tool reads.",
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
        "pl": "Usunąć tę domenę z monitoringu? Zebrane dane historyczne zostaną w bazie.",
        "en": "Remove this domain from monitoring? Collected history stays in the database.",
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

    # --- Acknowledgement ------------------------------------------------------
    "ack.button": {"pl": "Oznacz jako obsłużone", "en": "Mark as handled"},
    "ack.undo": {"pl": "Cofnij", "en": "Undo"},
    "ack.marked": {"pl": "Obsłużone", "en": "Handled"},
    "ack.marked_at": {"pl": "oznaczone {when}", "en": "marked {when}"},
    "ack.note_placeholder": {
        "pl": "notatka opcjonalnie, np. czekam na delisting",
        "en": "optional note, e.g. waiting for delisting",
    },
    "ack.explainer": {
        "pl": "Obsłużone flagi nie podbijają domeny na górę listy, ale zostają widoczne. "
        "Jeśli problem wystąpi ponownie, oznaczenie samo się cofnie.",
        "en": "Handled flags stop pushing a domain up the list but stay visible. "
        "If the problem happens again, the mark clears itself.",
    },
    "ack.section": {"pl": "Obsłużone", "en": "Handled"},

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
