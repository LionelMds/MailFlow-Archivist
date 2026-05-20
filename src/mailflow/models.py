from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

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


Usefulness = Literal["important", "normal", "faible", "inutile", "a_verifier"]
AiTargetFolder = Literal[
    "Correspondance",
    "Fournisseurs/Demande de prix",
    "Fournisseurs/Commande",
    "A verifier",
    "Ne pas archiver",
]
DuplicateStatus = Literal["none", "same_file_exists", "already_archived"]


class AiMailClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive: bool
    usefulness: Usefulness
    mail_type: Literal[
        "demande_de_prix",
        "devis",
        "commande",
        "facture",
        "correspondance_generale",
        "technique",
        "plan",
        "livraison",
        "administratif",
        "inutile_ou_faible_valeur",
        "a_verifier",
    ]
    interlocutor: Literal[
        "client",
        "fournisseur",
        "intervenant_externe",
        "interne",
        "inconnu",
    ]
    target_folder: AiTargetFolder
    confidence: float = Field(ge=0.0, le=1.0)
    short_summary: str = Field(max_length=120)
    reason: str = Field(max_length=200)

    @model_validator(mode="after")
    def enforce_business_constraints(self) -> AiMailClassification:
        if self.confidence < 0.80 and self.target_folder != "A verifier":
            msg = "target_folder must be 'A verifier' when confidence is below 0.80"
            raise ValueError(msg)
        if self.usefulness == "inutile" and self.archive:
            msg = "archive must be false when usefulness is 'inutile'"
            raise ValueError(msg)
        return self


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
