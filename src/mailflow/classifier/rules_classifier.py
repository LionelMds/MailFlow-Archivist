from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from mailflow.models import Direction, InterlocutorType, MailMetadata, MailType, RuleClassification


@dataclass(frozen=True)
class KeywordRule:
    name: str
    mail_type: MailType
    patterns: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class RuleMatch:
    rule: KeywordRule
    terms: tuple[str, ...]


RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        "demande_de_prix",
        MailType.DEMANDE_DE_PRIX,
        (
            r"\bdemande de prix\b",
            r"\bdemande d[' ]offre(?: de prix)?\b",
            r"\bappel d[' ]offres?\b",
            r"\bdemande de devis\b",
            r"\brequest for quote\b",
            r"\brequest for quotation\b",
            r"\brfq\b",
            r"\bpreisanfrage\b",
            r"\boffertanfrage\b",
            r"\banfrage (?:fur |fuer )?(?:preis|angebot|offerte)\b",
            r"\banfrage\b.*\b(?:angebot|offerte)\b",
            r"\brichiesta (?:di )?(?:offerta|preventivo)\b",
            r"\brdo\b",
        ),
        0.93,
    ),
    KeywordRule(
        "devis",
        MailType.DEVIS,
        (
            r"\bdevis\b",
            r"\boffre\b",
            r"\boffres?\b",
            r"\bsoumission\b",
            r"\bquotation\b",
            r"\bquote\b",
            r"\boffer\b",
            r"\bestimate\b",
            r"\bcost estimate\b",
            r"\boffert(?:e|en)?\b",
            r"\bangebot(?:e|en)?\b",
            r"\bkostenvoranschlag\b",
            r"\bofferta\b",
            r"\bofferte\b",
            r"\bpreventivo\b",
        ),
        0.88,
    ),
    KeywordRule(
        "commande",
        MailType.COMMANDE,
        (
            r"\bcommande\b",
            r"\bcomande\b",
            r"\bbon de commande\b",
            r"\bordres? d[' ]achat\b",
            r"\border\b",
            r"\bpurchase order\b",
            r"\bpo\b",
            r"confirmation de commande",
            r"\bbestellung\b",
            r"\bbestell(?:ung)?s?bestatigung\b",
            r"\bauftrag\b",
            r"\bauftragsbestatigung\b",
            r"\bab[-_ ]?\d{3,}\b",
            r"\bordine\b",
            r"\bordine d[' ]acquisto\b",
            r"\bconferma (?:d[' ]ordine|ordine)\b",
        ),
        0.95,
    ),
    KeywordRule(
        "facture",
        MailType.FACTURE,
        (
            r"\bfacture\b",
            r"\bnote d[' ]honoraires\b",
            r"\binvoice\b",
            r"\brechnung\b",
            r"\bfaktura\b",
            r"\bfattura\b",
        ),
        0.94,
    ),
    KeywordRule(
        "livraison",
        MailType.LIVRAISON,
        (
            r"\blivraison\b",
            r"\bbon de livraison\b",
            r"\bdelivery\b",
            r"\bdelivery note\b",
            r"\bshipment\b",
            r"\bshipping\b",
            r"\bexpedition\b",
            r"\blieferung\b",
            r"\blieferschein\b",
            r"\bversand\b",
            r"\bsendung\b",
            r"\bconsegna\b",
            r"\bspedizione\b",
            r"\bddt\b",
        ),
        0.86,
    ),
    KeywordRule(
        "plan",
        MailType.PLAN,
        (
            r"\bplan\b",
            r"\bplans\b",
            r"\bdessin\b",
            r"\bdrawing\b",
            r"\bzeichnung(?:en)?\b",
            r"\bplane\b",
            r"\bplaene\b",
            r"\bdisegno\b",
            r"\bdwg\b",
            r"\bdxf\b",
            r"\bpdf plan\b",
            r"\bcad\b",
            r"\bstp\b",
            r"\bstep\b",
        ),
        0.87,
    ),
    KeywordRule(
        "technique",
        MailType.TECHNIQUE,
        (
            r"\bdemande d[' ]etude\b",
            r"\betude statique\b",
            r"\bcalcul statique\b",
            r"\bstatic calculation\b",
            r"\bstatic study\b",
            r"\bstatik\b",
            r"\bstatische berechnung\b",
            r"\bcalcolo statico\b",
        ),
        0.86,
    ),
)

LOW_VALUE_PATTERNS = (
    r"^\s*merci\s*[.!]?\s*$",
    r"^\s*danke\s*[.!]?\s*$",
    r"^\s*grazie\s*[.!]?\s*$",
    r"^\s*thanks?\s*[.!]?\s*$",
    r"\bbien recu\b",
    r"\bgut erhalten\b",
    r"\berhalten\b",
    r"\breceived\b",
    r"\bricevuto\b",
    r"^\s*ok\s*[.!]?\s*$",
    r"^\s*i\.?o\.?\s*$",
    r"\bnewsletter\b",
    r"\bautomatic repl(y|ies)\b",
    r"\breponse automatique\b",
    r"\bautomatische antwort\b",
    r"\brisposta automatica\b",
)


def classify_mail(
    mail: MailMetadata,
    *,
    ignored_terms: list[str] | None = None,
) -> RuleClassification:
    return classify_text(
        subject=mail.subject,
        body_excerpt=mail.body_excerpt,
        attachment_names=mail.attachment_names,
        sender=mail.sender_email or mail.sender_name,
        recipients=mail.recipients,
        direction=mail.direction,
        ignored_terms=ignored_terms,
    )


def classify_text(
    *,
    subject: str,
    body_excerpt: str,
    attachment_names: list[str] | None = None,
    sender: str = "",
    recipients: list[str] | None = None,
    direction: Direction = Direction.RECEIVED,
    ignored_terms: list[str] | None = None,
) -> RuleClassification:
    attachments = " ".join(attachment_names or [])
    corpus = _normalize_text(" ".join([subject, body_excerpt, attachments]))
    normalized_ignored_terms = [_normalize_text(term) for term in ignored_terms or [] if term]
    matched = [
        match
        for rule in RULES
        if (match := _match_rule(rule, corpus, normalized_ignored_terms)) is not None
    ]

    low_value_corpus = _normalize_text("\n".join([subject, body_excerpt]))
    low_value = [
        match.group(0).strip()
        for pattern in LOW_VALUE_PATTERNS
        for match in re.finditer(pattern, low_value_corpus, flags=re.IGNORECASE | re.MULTILINE)
    ]
    if low_value and not matched:
        return RuleClassification(
            suggested_type=MailType.INUTILE_OU_FAIBLE_VALEUR,
            suggested_interlocutor=_infer_interlocutor(
                sender,
                None,
                recipients=recipients or [],
                direction=direction,
            ),
            likely_archive=False,
            confidence=0.86,
            matched_rules=["faible_valeur"],
            matched_terms=_dedupe_terms(low_value),
        )

    if not matched:
        return RuleClassification(
            suggested_type=None,
            suggested_interlocutor=_infer_interlocutor(
                sender,
                None,
                recipients=recipients or [],
                direction=direction,
            ),
            likely_archive=None,
            confidence=0.0,
            matched_rules=[],
        )

    best = max(matched, key=lambda match: match.rule.confidence)
    return RuleClassification(
        suggested_type=best.rule.mail_type,
        suggested_interlocutor=_infer_interlocutor(
            sender,
            best.rule.mail_type,
            recipients=recipients or [],
            direction=direction,
        ),
        likely_archive=True,
        confidence=best.rule.confidence,
        matched_rules=[match.rule.name for match in matched],
        matched_terms=_dedupe_terms(
            term
            for match in matched
            for term in match.terms
        ),
    )


def _match_rule(
    rule: KeywordRule,
    corpus: str,
    ignored_terms: list[str],
) -> RuleMatch | None:
    terms = []
    for pattern in rule.patterns:
        for match in re.finditer(pattern, corpus, flags=re.IGNORECASE):
            term = match.group(0).strip()
            if _is_ignored_match(term, ignored_terms):
                continue
            terms.append(term)
    if not terms:
        return None
    return RuleMatch(rule=rule, terms=tuple(_dedupe_terms(terms)))


def _is_ignored_match(term: str, ignored_terms: list[str]) -> bool:
    normalized = _normalize_text(term)
    return any(ignored and ignored in normalized for ignored in ignored_terms)


def _dedupe_terms(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for term in terms:
        text = str(term).strip()
        key = _normalize_text(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _infer_interlocutor(
    sender: str,
    mail_type: MailType | None,
    *,
    recipients: list[str],
    direction: Direction,
) -> InterlocutorType:
    if direction == Direction.SENT:
        if recipients and all(_is_internal_address(recipient) for recipient in recipients):
            return InterlocutorType.INTERNE
        if mail_type in {MailType.DEMANDE_DE_PRIX, MailType.DEVIS, MailType.COMMANDE}:
            return InterlocutorType.FOURNISSEUR
        if mail_type == MailType.TECHNIQUE:
            return InterlocutorType.INTERVENANT_EXTERNE
        if recipients:
            return InterlocutorType.INTERVENANT_EXTERNE

    normalized = sender.lower()
    if _is_internal_address(normalized):
        return InterlocutorType.INTERNE
    if mail_type in {MailType.DEMANDE_DE_PRIX, MailType.DEVIS, MailType.COMMANDE}:
        return InterlocutorType.FOURNISSEUR
    return InterlocutorType.INCONNU


def _is_internal_address(value: str) -> bool:
    normalized = value.lower()
    return "balzmetal" in normalized or "balzmetalsa" in normalized


def _normalize_text(value: str) -> str:
    normalized = value.replace("\u2019", "'").lower()
    decomposed = unicodedata.normalize("NFKD", normalized)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
