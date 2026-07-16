from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MailType(StrEnum):
    DEMANDE_DE_PRIX = "demande_de_prix"
    DEVIS = "devis"
    COMMANDE = "commande"
    FACTURE = "facture"
    CORRESPONDANCE_GENERALE = "correspondance_generale"
    TECHNIQUE = "technique"
    PLAN = "plan"
    LIVRAISON = "livraison"
    ADMINISTRATIF = "administratif"
    INUTILE_OU_FAIBLE_VALEUR = "inutile_ou_faible_valeur"
    A_VERIFIER = "a_verifier"


class RoutingCategory(StrEnum):
    CORRESPONDANCE = "Correspondance"
    DEMANDE_DE_PRIX = "Demande de prix"
    COMMANDE = "Commande"


class InterlocutorType(StrEnum):
    CLIENT = "client"
    FOURNISSEUR = "fournisseur"
    INTERVENANT_EXTERNE = "intervenant_externe"
    INTERNE = "interne"
    INCONNU = "inconnu"


class Direction(StrEnum):
    RECEIVED = "received"
    SENT = "sent"


class AiMode(StrEnum):
    DISABLED = "disabled"
    AMBIGUOUS_ONLY = "ambiguous_only"
    ALL = "all"


class PreviewAction(StrEnum):
    ARCHIVE = "archive"
    ARCHIVED = "archived"
    IGNORE = "ignore"
    REVIEW = "review"


class ProjectRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: str
    year: str
    sequence: str
    subproject: str | None = None

    @property
    def main_number(self) -> str:
        return f"{self.year}-{self.sequence}"

    @property
    def is_subproject(self) -> bool:
        return self.subproject is not None


class OutlookAccount(BaseModel):
    display_name: str
    smtp_address: str | None = None


class MailMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    conversation_id: str | None = None
    internet_message_id: str | None = None
    project_number: str
    outlook_folder: str
    direction: Direction
    subject: str = ""
    sender_name: str = ""
    sender_email: str = ""
    recipients: list[str] = Field(default_factory=list)
    sent_at: datetime
    attachment_names: list[str] = Field(default_factory=list)
    body_excerpt: str = ""
    categories: list[str] = Field(default_factory=list)
    archive_order: int | None = Field(default=None, ge=1)


class RuleClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_type: MailType | None = None
    suggested_interlocutor: InterlocutorType | None = None
    likely_archive: bool | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    matched_rules: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)


DuplicateStatus = Literal["none", "same_file_exists", "already_archived"]


class AiMailClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["Correspondance", "Demande de prix", "Commande"]
    organization_role: Literal["client", "fournisseur", "inconnu"]
    organization_name: str | None = Field(max_length=80)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_review: bool
    short_summary: str = Field(max_length=120)
    reason: str = Field(max_length=200)
    evidence: list[str] = Field(max_length=3)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_output(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "category" in value:
            return value
        legacy = dict(value)
        mail_type = str(legacy.pop("mail_type", "a_verifier"))
        target = str(legacy.pop("target_folder", "A verifier"))
        interlocutor = str(legacy.pop("interlocutor", "inconnu"))
        archive = bool(legacy.pop("archive", False))
        legacy.pop("usefulness", None)
        category = _routing_category_from_legacy(mail_type, target)
        return {
            **legacy,
            "category": category.value,
            "organization_role": (
                interlocutor if interlocutor in {"client", "fournisseur"} else "inconnu"
            ),
            "organization_name": None,
            "requires_review": target == "A verifier" or not archive,
            "evidence": [],
        }

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, value: list[str]) -> list[str]:
        return [item.strip()[:120] for item in value if item.strip()][:3]

    @model_validator(mode="after")
    def enforce_review_constraints(self) -> AiMailClassification:
        if self.confidence < 0.80 or self.organization_role == "inconnu":
            self.requires_review = True
        return self

    @property
    def mail_type(self) -> str:
        return mail_type_for_routing_category(RoutingCategory(self.category)).value

    @property
    def interlocutor(self) -> str:
        return self.organization_role

    @property
    def target_folder(self) -> str:
        if self.requires_review:
            return "A verifier"
        return target_folder_for_routing_category(
            RoutingCategory(self.category),
            InterlocutorType(self.organization_role),
        )

    @property
    def archive(self) -> bool:
        return not self.requires_review and self.target_folder != "A verifier"

    @property
    def usefulness(self) -> str:
        return "a_verifier" if self.requires_review else "normal"


class ArchiveDecision(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    mail_id: str
    project_number: str
    archive: bool
    requires_review: bool
    mail_type: MailType
    interlocutor: InterlocutorType
    target_relative_folder: str
    target_path: Path
    confidence: float = Field(ge=0.0, le=1.0)
    duplicate_status: DuplicateStatus
    reason: str

    @field_validator("target_relative_folder")
    @classmethod
    def normalize_target_relative_folder(cls, value: str) -> str:
        return value.replace("\\", "/").strip("/")


class ArchivedMailRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    outlook_entry_id: str
    conversation_id: str | None = None
    internet_message_id: str | None = None
    project_number: str
    subject: str = ""
    sender: str = ""
    sent_at: datetime
    msg_path: Path
    target_folder: str
    classification: MailType
    confidence: float = Field(ge=0.0, le=1.0)
    archived_at: datetime


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: RuleClassification
    ai: AiMailClassification | None = None


class PreviewRow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    mail: MailMetadata
    classification: ClassificationResult
    decision: ArchiveDecision
    action: PreviewAction


class ManualClassificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mail_type: MailType
    interlocutor: InterlocutorType
    target_relative_folder: str
    learning_term: str | None = None
    misleading_term: str | None = None
    manual_required: bool = False

    @field_validator("target_relative_folder")
    @classmethod
    def normalize_target_relative_folder(cls, value: str) -> str:
        return value.replace("\\", "/").strip()

    @field_validator("learning_term")
    @classmethod
    def normalize_learning_term(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("misleading_term")
    @classmethod
    def normalize_misleading_term(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ManualLearningSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mail_id: str
    project_number: str
    subject: str
    selected_mail_type: MailType
    selected_interlocutor: InterlocutorType
    selected_target_folder: str
    learning_term: str | None = None
    misleading_term: str | None = None
    manual_required: bool
    created_at: datetime
    organization_name: str | None = None
    primary_email: str | None = None


class VerifiedRoutingExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_number: str
    subject: str
    organization_name: str
    organization_role: InterlocutorType
    category: RoutingCategory

    @model_validator(mode="after")
    def validate_business_role(self) -> VerifiedRoutingExample:
        if self.organization_role not in {
            InterlocutorType.CLIENT,
            InterlocutorType.FOURNISSEUR,
        }:
            msg = "A verified routing example requires a client or supplier role"
            raise ValueError(msg)
        return self


def mail_type_for_routing_category(category: RoutingCategory) -> MailType:
    return {
        RoutingCategory.CORRESPONDANCE: MailType.CORRESPONDANCE_GENERALE,
        RoutingCategory.DEMANDE_DE_PRIX: MailType.DEMANDE_DE_PRIX,
        RoutingCategory.COMMANDE: MailType.COMMANDE,
    }[category]


def routing_category_for_mail_type(mail_type: MailType) -> RoutingCategory:
    if mail_type in {MailType.DEMANDE_DE_PRIX, MailType.DEVIS}:
        return RoutingCategory.DEMANDE_DE_PRIX
    if mail_type in {MailType.COMMANDE, MailType.FACTURE, MailType.LIVRAISON}:
        return RoutingCategory.COMMANDE
    return RoutingCategory.CORRESPONDANCE


def target_folder_for_routing_category(
    category: RoutingCategory,
    interlocutor: InterlocutorType,
) -> str:
    if interlocutor == InterlocutorType.CLIENT and category == RoutingCategory.CORRESPONDANCE:
        return "Correspondance"
    if interlocutor == InterlocutorType.FOURNISSEUR:
        if category == RoutingCategory.DEMANDE_DE_PRIX:
            return "Fournisseurs/Demande de prix"
        if category == RoutingCategory.COMMANDE:
            return "Fournisseurs/Commande"
    return "A verifier"


def _routing_category_from_legacy(mail_type: str, target: str) -> RoutingCategory:
    if target.startswith("Fournisseurs/Commande") or mail_type in {
        MailType.COMMANDE.value,
        MailType.FACTURE.value,
        MailType.LIVRAISON.value,
    }:
        return RoutingCategory.COMMANDE
    if target.startswith("Fournisseurs/Demande de prix") or mail_type in {
        MailType.DEMANDE_DE_PRIX.value,
        MailType.DEVIS.value,
    }:
        return RoutingCategory.DEMANDE_DE_PRIX
    return RoutingCategory.CORRESPONDANCE
