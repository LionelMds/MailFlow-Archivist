from __future__ import annotations

from mailflow.classifier.rules_classifier import classify_text
from mailflow.models import InterlocutorType, MailType


def test_detects_demande_de_prix() -> None:
    result = classify_text(
        subject="RFQ garde-corps",
        body_excerpt="Merci de nous transmettre votre demande de prix.",
        attachment_names=[],
    )

    assert result.suggested_type == MailType.DEMANDE_DE_PRIX
    assert result.suggested_interlocutor == InterlocutorType.FOURNISSEUR
    assert result.likely_archive is True
    assert result.confidence >= 0.8
    assert "RFQ" in result.matched_terms or "demande de prix" in result.matched_terms


def test_detects_invoice() -> None:
    result = classify_text(
        subject="Invoice 123",
        body_excerpt="Please find attached the invoice.",
        attachment_names=["invoice.pdf"],
    )

    assert result.suggested_type == MailType.FACTURE
    assert result.likely_archive is True


def test_detects_swiss_german_offerte_as_devis() -> None:
    result = classify_text(
        subject="Offerte 127.742 2025-4893-LMDS",
        body_excerpt="Gerne senden wir Ihnen unsere Offerte.",
        attachment_names=["Offerte_127742.pdf"],
        sender="verkauf@lieferant.test",
    )

    assert result.suggested_type == MailType.DEVIS
    assert result.suggested_interlocutor == InterlocutorType.FOURNISSEUR
    assert result.likely_archive is True


def test_detects_multilingual_quote_terms() -> None:
    samples = [
        "Soumission garde-corps",
        "Quotation for railing",
        "Angebot Profile",
        "Preventivo profili",
    ]

    for subject in samples:
        result = classify_text(subject=subject, body_excerpt="", attachment_names=[])
        assert result.suggested_type == MailType.DEVIS


def test_detects_multilingual_rfq_terms() -> None:
    samples = [
        "Appel d'offres marquise",
        "Request for quotation - profiles",
        "Preisanfrage Profile",
        "Richiesta di offerta profili",
    ]

    for subject in samples:
        result = classify_text(subject=subject, body_excerpt="", attachment_names=[])
        assert result.suggested_type == MailType.DEMANDE_DE_PRIX


def test_detects_multilingual_order_terms() -> None:
    samples = [
        "Bon de commande 12345",
        "Purchase order PO-12345",
        "Bestellung Profile",
        "Ordine d'acquisto profili",
        "Auftragsbestatigung AB-10223138",
    ]

    for subject in samples:
        result = classify_text(subject=subject, body_excerpt="", attachment_names=[])
        assert result.suggested_type == MailType.COMMANDE


def test_detects_multilingual_delivery_and_invoice_terms() -> None:
    delivery = classify_text(subject="Lieferschein 123", body_excerpt="", attachment_names=[])
    invoice = classify_text(subject="Fattura 123", body_excerpt="", attachment_names=[])

    assert delivery.suggested_type == MailType.LIVRAISON
    assert invoice.suggested_type == MailType.FACTURE


def test_detects_low_value_mail() -> None:
    result = classify_text(subject="Re: projet", body_excerpt="ok", attachment_names=[])

    assert result.suggested_type == MailType.INUTILE_OU_FAIBLE_VALEUR
    assert result.likely_archive is False


def test_ambiguous_mail_has_no_local_suggestion() -> None:
    result = classify_text(
        subject="Question",
        body_excerpt="Bonjour, pouvez-vous regarder ?",
        attachment_names=[],
    )

    assert result.suggested_type is None
    assert result.likely_archive is None
    assert result.confidence == 0


def test_ignored_misleading_term_is_not_used_for_classification() -> None:
    result = classify_text(
        subject="Offre spéciale",
        body_excerpt="Bonjour, ceci est une annonce sans lien projet.",
        attachment_names=[],
        ignored_terms=["offre"],
    )

    assert result.suggested_type is None
    assert result.matched_terms == []
