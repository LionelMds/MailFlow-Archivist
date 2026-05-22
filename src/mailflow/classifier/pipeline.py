from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from mailflow.classifier.decision_engine import ArchiveState, decide_archive
from mailflow.classifier.rules_classifier import classify_mail
from mailflow.core.contact_directory import email_domain, split_contact
from mailflow.core.correspondence_hierarchy import (
    OrganizationDirectoryProtocol,
    apply_correspondence_hierarchy,
)
from mailflow.core.manual_review import (
    LearnedClassificationRule,
    LearnedMisleadingTerm,
    classify_with_learned_terms,
)
from mailflow.models import (
    AiMailClassification,
    AiMode,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)


class AiClassifierProtocol(Protocol):
    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, str] | None = None,
    ) -> AiMailClassification:
        ...


@runtime_checkable
class ProjectRoleDirectoryProtocol(Protocol):
    def interlocutor_for_email(
        self,
        project_number: str,
        email: str,
    ) -> InterlocutorType | None:
        ...


def should_call_ai(rule: RuleClassification, *, ai_mode: AiMode, threshold: float) -> bool:
    if ai_mode == AiMode.DISABLED:
        return False
    if ai_mode == AiMode.ALL:
        return True
    return (
        rule.suggested_type is None
        or rule.likely_archive is None
        or rule.confidence < threshold
    )


class ClassificationPipeline:
    def __init__(
        self,
        *,
        projects_root: Path,
        archive_state: ArchiveState | None = None,
        ai_mode: AiMode = AiMode.AMBIGUOUS_ONLY,
        ai_classifier: AiClassifierProtocol | None = None,
        rule_confidence_threshold: float = 0.80,
        decision_confidence_threshold: float = 0.80,
        include_body_for_ai: bool = True,
        privacy_mask_phone_numbers: bool = False,
        learned_rules: list[LearnedClassificationRule] | None = None,
        misleading_terms: list[LearnedMisleadingTerm] | None = None,
        organization_directory: OrganizationDirectoryProtocol | None = None,
        client_email_domains: Sequence[str] = ("gva.ch",),
    ) -> None:
        self.projects_root = projects_root
        self.archive_state = archive_state
        self.ai_mode = ai_mode
        self.ai_classifier = ai_classifier
        self.rule_confidence_threshold = rule_confidence_threshold
        self.decision_confidence_threshold = decision_confidence_threshold
        self.include_body_for_ai = include_body_for_ai
        self.privacy_mask_phone_numbers = privacy_mask_phone_numbers
        self.learned_rules = learned_rules or []
        self.misleading_terms = misleading_terms or []
        self.organization_directory = organization_directory
        self.client_email_domains = tuple(
            domain.strip().casefold()
            for domain in client_email_domains
            if domain.strip()
        )

    def preview(self, mails: list[MailMetadata]) -> list[PreviewRow]:
        rows = [self.preview_one(mail) for mail in mails]
        return apply_correspondence_hierarchy(
            rows,
            projects_root=self.projects_root,
            organization_directory=self.organization_directory,
        )

    def preview_one(self, mail: MailMetadata) -> PreviewRow:
        mail = apply_known_client_contact_hint(
            mail,
            client_email_domains=self.client_email_domains,
        )
        rule = classify_with_learned_terms(mail, self.learned_rules) or classify_mail(
            mail,
            ignored_terms=[term.term for term in self.misleading_terms],
        )
        rule = apply_known_client_domain_priority(
            mail,
            rule,
            client_email_domains=self.client_email_domains,
            role_directory=self.organization_directory,
        )
        ai = self._maybe_classify_with_ai(mail, rule)
        ai = apply_participant_role_priority(
            mail,
            ai,
            rule,
            client_email_domains=self.client_email_domains,
            role_directory=self.organization_directory,
        )
        decision = decide_archive(
            mail,
            projects_root=self.projects_root,
            rule=rule,
            ai=ai,
            archive_state=self.archive_state,
            confidence_threshold=self.decision_confidence_threshold,
        )
        return PreviewRow(
            mail=mail,
            classification=ClassificationResult(rule=rule, ai=ai),
            decision=decision,
            action=action_from_decision(
                archive=decision.archive,
                requires_review=decision.requires_review,
            ),
        )

    def _maybe_classify_with_ai(
        self,
        mail: MailMetadata,
        rule: RuleClassification,
    ) -> AiMailClassification | None:
        if not should_call_ai(rule, ai_mode=self.ai_mode, threshold=self.rule_confidence_threshold):
            return None
        if self.ai_classifier is None:
            return None
        try:
            return self.ai_classifier.classify(
                mail,
                include_body=self.include_body_for_ai,
                privacy_mask_phone_numbers=self.privacy_mask_phone_numbers,
            )
        except Exception:
            return None


def action_from_decision(*, archive: bool, requires_review: bool) -> PreviewAction:
    if requires_review:
        return PreviewAction.REVIEW
    if archive:
        return PreviewAction.ARCHIVE
    return PreviewAction.IGNORE


def apply_known_client_domain_priority(
    mail: MailMetadata,
    rule: RuleClassification,
    *,
    client_email_domains: Sequence[str],
    role_directory: object | None = None,
) -> RuleClassification:
    role = _role_from_project_directory(mail, role_directory)
    if role is None and _mail_matches_client_domain(mail, client_email_domains):
        role = InterlocutorType.CLIENT
    if role is None:
        return rule
    return rule.model_copy(update={"suggested_interlocutor": role})


def apply_known_client_contact_hint(
    mail: MailMetadata,
    *,
    client_email_domains: Sequence[str],
) -> MailMetadata:
    if mail.sender_email.strip() or not client_email_domains:
        return mail
    if mail.direction != Direction.RECEIVED:
        return mail
    email = _first_email_for_known_client_domain(mail.body_excerpt, client_email_domains)
    if email is None:
        return mail
    return mail.model_copy(update={"sender_email": email})


def apply_participant_role_priority(
    mail: MailMetadata,
    ai: AiMailClassification | None,
    rule: RuleClassification,
    *,
    client_email_domains: Sequence[str],
    role_directory: object | None = None,
) -> AiMailClassification | None:
    if ai is None:
        return ai
    role = _role_from_project_directory(mail, role_directory)
    if role is None and _mail_matches_client_domain(mail, client_email_domains):
        role = InterlocutorType.CLIENT
    if role is not None:
        return _copy_ai_with_interlocutor(
            ai,
            role,
            reason_note=f"Role projet {role.value} prioritaire.",
        )
    if mail.direction != Direction.SENT or not mail.recipients:
        return ai
    primary_recipient = mail.recipients[0]
    if _is_internal_contact(primary_recipient):
        return _copy_ai_with_interlocutor(
            ai,
            InterlocutorType.INTERNE,
            reason_note="Destinataire principal interne.",
        )
    mail_type = MailType(ai.mail_type)
    current_interlocutor = InterlocutorType(ai.interlocutor)
    corrected_interlocutor = _external_primary_interlocutor(
        mail_type,
        current_interlocutor,
        rule.suggested_interlocutor,
    )
    if corrected_interlocutor == current_interlocutor:
        return ai
    return _copy_ai_with_interlocutor(
        ai,
        corrected_interlocutor,
        reason_note="Destinataire principal externe prioritaire.",
    )


def _external_primary_interlocutor(
    mail_type: MailType,
    current_interlocutor: InterlocutorType,
    rule_interlocutor: InterlocutorType | None,
) -> InterlocutorType:
    if current_interlocutor == InterlocutorType.FOURNISSEUR:
        return current_interlocutor
    if mail_type in {
        MailType.DEMANDE_DE_PRIX,
        MailType.DEVIS,
        MailType.COMMANDE,
        MailType.FACTURE,
        MailType.LIVRAISON,
    }:
        return InterlocutorType.FOURNISSEUR
    if rule_interlocutor in {
        InterlocutorType.CLIENT,
        InterlocutorType.INTERVENANT_EXTERNE,
    }:
        return rule_interlocutor
    if current_interlocutor in {
        InterlocutorType.CLIENT,
        InterlocutorType.INTERVENANT_EXTERNE,
    }:
        return current_interlocutor
    return InterlocutorType.CLIENT


def _copy_ai_with_interlocutor(
    ai: AiMailClassification,
    interlocutor: InterlocutorType,
    *,
    reason_note: str,
) -> AiMailClassification:
    mail_type = MailType(ai.mail_type)
    target_folder = _target_folder_for_corrected_interlocutor(
        mail_type,
        interlocutor,
        ai.target_folder,
    )
    archive = ai.archive and target_folder != "A verifier"
    return ai.model_copy(
        update={
            "archive": archive,
            "interlocutor": interlocutor.value,
            "target_folder": target_folder,
            "reason": _append_reason_note(ai.reason, reason_note),
        }
    )


def _target_folder_for_corrected_interlocutor(
    mail_type: MailType,
    interlocutor: InterlocutorType,
    current_target: str,
) -> str:
    if interlocutor == InterlocutorType.CLIENT:
        return "Correspondance"
    if interlocutor == InterlocutorType.FOURNISSEUR:
        if mail_type in {MailType.DEMANDE_DE_PRIX, MailType.DEVIS}:
            return "Fournisseurs/Demande de prix"
        if mail_type in {MailType.COMMANDE, MailType.FACTURE, MailType.LIVRAISON}:
            return "Fournisseurs/Commande"
        return "A verifier"
    if current_target in {"Fournisseurs/Demande de prix", "Fournisseurs/Commande"}:
        return "A verifier"
    return current_target


def _append_reason_note(reason: str, note: str) -> str:
    if note in reason:
        return reason
    joined = f"{reason.rstrip()} {note}".strip()
    return joined[:200]


def _is_internal_contact(contact: str) -> bool:
    lowered = contact.casefold()
    return "balzmetal.ch" in lowered or "balzmetalsa.onmicrosoft.com" in lowered


def _mail_matches_client_domain(
    mail: MailMetadata,
    client_email_domains: Sequence[str],
) -> bool:
    if not client_email_domains:
        return False
    domains = set(client_email_domains)
    return any(
        _domain_matches_known_client(domain, domains)
        for domain in _participant_domains_for_role_detection(mail)
    )


def _role_from_project_directory(
    mail: MailMetadata,
    role_directory: object | None,
) -> InterlocutorType | None:
    if not isinstance(role_directory, ProjectRoleDirectoryProtocol):
        return None
    for email in _participant_emails_for_role_detection(mail):
        role = role_directory.interlocutor_for_email(mail.project_number, email)
        if role is not None and role != InterlocutorType.INCONNU:
            return role
    return None


def _participant_domains_for_role_detection(mail: MailMetadata) -> list[str]:
    contacts = _priority_contacts_for_role_detection(mail)
    domains: list[str] = []
    for contact in contacts:
        domains.extend(_domains_from_text(contact))
    return domains


def _participant_emails_for_role_detection(mail: MailMetadata) -> list[str]:
    contacts = _priority_contacts_for_role_detection(mail)
    emails: list[str] = []
    for contact in contacts:
        _display_name, parsed_email = split_contact(contact)
        if parsed_email and parsed_email not in emails:
            emails.append(parsed_email)
        for match in re.finditer(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", contact):
            email = match.group(0).casefold()
            if email not in emails:
                emails.append(email)
    return emails


def _priority_contacts_for_role_detection(mail: MailMetadata) -> list[str]:
    if mail.direction == Direction.SENT:
        first_recipient = mail.recipients[0] if mail.recipients else ""
        return [first_recipient]
    return [mail.sender_email, mail.sender_name, mail.body_excerpt]


def _domains_from_text(value: str) -> list[str]:
    _display_name, parsed_email = split_contact(value)
    emails = [parsed_email] if parsed_email else []
    emails.extend(
        match.group(0)
        for match in re.finditer(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value)
    )
    domains: list[str] = []
    for email in emails:
        domain = email_domain(email)
        if domain is not None and domain not in domains:
            domains.append(domain)
    return domains


def _first_email_for_known_client_domain(
    value: str,
    client_email_domains: Sequence[str],
) -> str | None:
    known_domains = {domain.casefold() for domain in client_email_domains}
    for match in re.finditer(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value):
        email = match.group(0).casefold()
        domain = email_domain(email)
        if domain is not None and _domain_matches_known_client(domain, known_domains):
            return email
    return None


def _domain_matches_known_client(domain: str, known_domains: set[str]) -> bool:
    normalized = domain.casefold()
    return any(
        normalized == known_domain or normalized.endswith(f".{known_domain}")
        for known_domain in known_domains
    )
