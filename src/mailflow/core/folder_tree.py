from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from mailflow.core.correspondence_hierarchy import is_safe_relative_folder, safe_folder_name
from mailflow.core.project_paths import local_project_path
from mailflow.models import PreviewRow

SPECIAL_FOLDERS = {"A verifier", "Ne pas archiver"}
FOLDER_SORT_PRIORITY = {
    "Correspondance": 0,
    "Fournisseurs": 1,
    "Demande de prix": 2,
    "Commande": 3,
    "A verifier": 90,
    "Ne pas archiver": 91,
}


@dataclass(frozen=True)
class FolderPathSummary:
    relative_folder: str
    mail_count: int


@dataclass(frozen=True)
class FolderTreeNode:
    name: str
    relative_folder: str
    mail_count: int
    children: tuple[FolderTreeNode, ...] = field(default_factory=tuple)


def folder_path_counts(rows: list[PreviewRow]) -> list[FolderPathSummary]:
    counts = Counter(row.decision.target_relative_folder for row in rows)
    return [
        FolderPathSummary(relative_folder=relative_folder, mail_count=mail_count)
        for relative_folder, mail_count in sorted(counts.items())
    ]


def build_folder_tree(rows: list[PreviewRow]) -> list[FolderTreeNode]:
    root: dict[str, object] = {"children": {}, "count": 0, "path": ""}
    for row in rows:
        parts = _folder_parts(row.decision.target_relative_folder)
        current = root
        for index, part in enumerate(parts):
            children = current["children"]
            assert isinstance(children, dict)
            relative_folder = "/".join(parts[: index + 1])
            child = children.setdefault(
                part,
                {"children": {}, "count": 0, "path": relative_folder},
            )
            assert isinstance(child, dict)
            child["count"] = int(child["count"]) + 1
            current = child
    children = root["children"]
    assert isinstance(children, dict)
    return _tree_nodes(children)


def folder_sort_key(relative_folder: str) -> tuple[tuple[int, str], ...]:
    return tuple(
        (FOLDER_SORT_PRIORITY.get(part, 50), part.casefold())
        for part in _folder_parts(relative_folder)
    )


def rename_folder_leaf(
    rows: list[PreviewRow],
    source_relative_folder: str,
    new_folder_name: str,
    *,
    projects_root: Path,
) -> list[PreviewRow]:
    target_relative_folder = renamed_leaf_path(source_relative_folder, new_folder_name)
    return rewrite_folder_prefix(
        rows,
        source_relative_folder,
        target_relative_folder,
        projects_root=projects_root,
        reason="Nom de dossier corrige dans l'arborescence.",
    )


def merge_folder(
    rows: list[PreviewRow],
    source_relative_folder: str,
    target_relative_folder: str,
    *,
    projects_root: Path,
) -> list[PreviewRow]:
    return rewrite_folder_prefix(
        rows,
        source_relative_folder,
        target_relative_folder,
        projects_root=projects_root,
        reason="Dossier fusionne dans l'arborescence.",
    )


def renamed_leaf_path(source_relative_folder: str, new_folder_name: str) -> str:
    source = _normalize_relative_folder(source_relative_folder)
    if source in SPECIAL_FOLDERS:
        msg = f"Dossier special non renommable: {source}"
        raise ValueError(msg)
    cleaned_name = safe_folder_name(new_folder_name)
    parts = _folder_parts(source)
    if not parts:
        msg = "Dossier source invalide"
        raise ValueError(msg)
    renamed_parts = [*parts[:-1], cleaned_name]
    target = "/".join(renamed_parts)
    _validate_rewrite_target(source, target)
    return target


def rewrite_folder_prefix(
    rows: list[PreviewRow],
    source_relative_folder: str,
    target_relative_folder: str,
    *,
    projects_root: Path,
    reason: str,
) -> list[PreviewRow]:
    source = _normalize_relative_folder(source_relative_folder)
    target = _normalize_relative_folder(target_relative_folder)
    _validate_rewrite_target(source, target)
    return [
        _rewrite_row_folder(row, source, target, projects_root=projects_root, reason=reason)
        for row in rows
    ]


def _rewrite_row_folder(
    row: PreviewRow,
    source: str,
    target: str,
    *,
    projects_root: Path,
    reason: str,
) -> PreviewRow:
    current = row.decision.target_relative_folder
    if current != source and not current.startswith(f"{source}/"):
        return row
    suffix = current[len(source) :].strip("/")
    new_relative = "/".join(part for part in [target, suffix] if part)
    project_path = local_project_path(projects_root, row.mail.project_number)
    target_path = (
        project_path
        if new_relative in SPECIAL_FOLDERS
        else project_path.joinpath(*new_relative.split("/"))
    )
    decision = row.decision.model_copy(
        update={
            "target_relative_folder": new_relative,
            "target_path": target_path,
            "reason": _append_reason(row.decision.reason, reason),
        }
    )
    return row.model_copy(update={"decision": decision})


def _append_reason(existing: str, reason: str) -> str:
    if reason in existing:
        return existing
    return f"{existing} {reason}".strip()


def _validate_rewrite_target(source: str, target: str) -> None:
    if source in SPECIAL_FOLDERS:
        msg = f"Dossier special non modifiable: {source}"
        raise ValueError(msg)
    if target in SPECIAL_FOLDERS:
        msg = f"Dossier cible reserve: {target}"
        raise ValueError(msg)
    if not is_safe_relative_folder(source) or not is_safe_relative_folder(target):
        msg = "Chemin de dossier invalide"
        raise ValueError(msg)
    if not source or not target:
        msg = "Chemin de dossier vide"
        raise ValueError(msg)
    if target == source or target.startswith(f"{source}/"):
        msg = "La destination ne peut pas etre identique ou enfant de la source"
        raise ValueError(msg)


def _folder_parts(relative_folder: str) -> list[str]:
    return [part for part in relative_folder.replace("\\", "/").strip("/").split("/") if part]


def _normalize_relative_folder(value: str) -> str:
    return "/".join(_folder_parts(value))


def _tree_nodes(raw_nodes: dict[str, object]) -> list[FolderTreeNode]:
    nodes = []
    for name, raw_node in sorted(raw_nodes.items(), key=lambda item: folder_sort_key(item[0])):
        assert isinstance(raw_node, dict)
        children = raw_node["children"]
        assert isinstance(children, dict)
        nodes.append(
            FolderTreeNode(
                name=name,
                relative_folder=str(raw_node["path"]),
                mail_count=int(raw_node["count"]),
                children=tuple(_tree_nodes(children)),
            )
        )
    return nodes
