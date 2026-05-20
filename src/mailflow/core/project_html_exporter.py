from __future__ import annotations

import base64
import html
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mailflow.core.filenames import build_archive_stem, build_attachment_filename
from mailflow.core.folder_tree import FolderTreeNode, build_folder_tree, folder_sort_key
from mailflow.core.project_paths import local_project_path
from mailflow.models import Direction, PreviewRow
from mailflow.outlook.attachments import (
    attachment_display_name,
    attachment_mime_type,
    is_inline_image_attachment,
)
from mailflow.outlook.scanner import iter_com_collection


@dataclass(frozen=True)
class HtmlAttachmentLink:
    name: str
    href: str | None
    path: Path | None


@dataclass(frozen=True)
class HtmlInlineImage:
    name: str
    data_uri: str


@dataclass(frozen=True)
class ProjectHtmlExportResult:
    project_number: str
    html_path: Path
    attachment_dir: Path
    attachment_paths: list[Path] = field(default_factory=list)
    mail_count: int = 0


@dataclass(frozen=True)
class HtmlMailEntry:
    order: int
    row: PreviewRow
    attachments: list[HtmlAttachmentLink]
    inline_images: list[HtmlInlineImage]


def export_project_correspondence_html(
    rows: Sequence[PreviewRow],
    outlook_items: Mapping[str, object],
    projects_root: Path,
    *,
    overwrite_html: bool = False,
) -> list[ProjectHtmlExportResult]:
    grouped = _rows_by_project(rows)
    if not grouped:
        return []

    planned_paths = {
        project_number: _project_html_path(projects_root, project_number)
        for project_number in grouped
    }
    for path in planned_paths.values():
        if path.exists() and not overwrite_html:
            raise FileExistsError(path)

    return [
        _export_one_project(
            project_number,
            project_rows,
            outlook_items,
            projects_root,
            planned_paths[project_number],
        )
        for project_number, project_rows in sorted(grouped.items())
    ]


def _export_one_project(
    project_number: str,
    rows: list[PreviewRow],
    outlook_items: Mapping[str, object],
    projects_root: Path,
    html_path: Path,
) -> ProjectHtmlExportResult:
    project_path = local_project_path(projects_root, project_number)
    if not project_path.exists():
        raise FileNotFoundError(project_path)

    correspondence_dir = html_path.parent
    correspondence_dir.mkdir(parents=True, exist_ok=True)
    attachment_dir = correspondence_dir / f"{project_number} - pieces jointes"
    ordered_rows = sorted(rows, key=lambda row: (row.mail.sent_at, row.mail.entry_id))
    entries: list[HtmlMailEntry] = []
    attachment_paths: list[Path] = []

    for order, row in enumerate(ordered_rows, start=1):
        item = outlook_items.get(row.mail.entry_id)
        mail_stem = build_archive_stem(order, row.mail.direction, row.mail.subject, max_length=120)
        links, inline_images = _export_attachment_links(
            row=row,
            item=item,
            attachment_dir=attachment_dir,
            mail_stem=mail_stem,
        )
        attachment_paths.extend([link.path for link in links if link.path is not None])
        entries.append(
            HtmlMailEntry(
                order=order,
                row=row,
                attachments=links,
                inline_images=inline_images,
            )
        )

    html_path.write_text(_render_project_html(project_number, entries), encoding="utf-8")
    return ProjectHtmlExportResult(
        project_number=project_number,
        html_path=html_path,
        attachment_dir=attachment_dir,
        attachment_paths=attachment_paths,
        mail_count=len(entries),
    )


def _export_attachment_links(
    *,
    row: PreviewRow,
    item: object | None,
    attachment_dir: Path,
    mail_stem: str,
) -> tuple[list[HtmlAttachmentLink], list[HtmlInlineImage]]:
    if item is None:
        return (
            [
                HtmlAttachmentLink(name=name, href=None, path=None)
                for name in row.mail.attachment_names
            ],
            [],
        )

    attachments = iter_com_collection(getattr(item, "Attachments", []))
    if not attachments:
        return (
            [
                HtmlAttachmentLink(name=name, href=None, path=None)
                for name in row.mail.attachment_names
            ],
            [],
        )

    links: list[HtmlAttachmentLink] = []
    inline_images: list[HtmlInlineImage] = []
    for attachment in attachments:
        original_name = attachment_display_name(attachment)
        if is_inline_image_attachment(attachment):
            inline_image = _inline_image_from_attachment(attachment, original_name)
            if inline_image is not None:
                inline_images.append(inline_image)
            continue
        attachment_dir.mkdir(parents=True, exist_ok=True)
        target = attachment_dir / build_attachment_filename(mail_stem, original_name)
        if not target.exists():
            attachment.SaveAsFile(str(target))
        links.append(
            HtmlAttachmentLink(
                name=original_name,
                href=_relative_attachment_href(attachment_dir, target),
                path=target,
            )
        )
    return links, inline_images


def _render_project_html(project_number: str, entries: list[HtmlMailEntry]) -> str:
    mail_types = sorted({entry.row.decision.mail_type.value for entry in entries})
    mail_folders = sorted(
        {entry.row.decision.target_relative_folder for entry in entries},
        key=folder_sort_key,
    )
    interlocutors = sorted({entry.row.decision.interlocutor.value for entry in entries})
    folder_tree = build_folder_tree([entry.row for entry in entries])
    folder_sections = _render_folder_sections(entries)
    folder_nav = _render_folder_nav(folder_tree, total_count=len(entries))
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(project_number)} - Correspondance projet</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5f6b7a;
      --line: #d8dee8;
      --accent: #1f6feb;
      --sent: #7c3aed;
      --received: #0f766e;
      --warn: #b45309;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      backdrop-filter: blur(10px);
    }}
    .header-inner {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 22px 14px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
      font-weight: 650;
      letter-spacing: 0;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) 150px 220px 220px 180px;
      gap: 10px;
    }}
    input, select {{
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 0 10px;
      font: inherit;
    }}
    main {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 18px 22px 36px;
    }}
    .summary {{
      display: flex;
      gap: 12px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 12px;
    }}
    .project-layout {{
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }}
    .folder-panel {{
      position: sticky;
      top: 104px;
      max-height: calc(100vh - 124px);
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
    }}
    .folder-panel h2 {{
      margin: 0 0 10px;
      font-size: 15px;
      font-weight: 650;
    }}
    .folder-tree {{
      list-style: none;
      padding-left: 0;
      margin: 0;
    }}
    .folder-tree ul {{
      list-style: none;
      padding-left: 14px;
      margin: 2px 0 4px;
      border-left: 1px solid var(--line);
    }}
    .folder-button {{
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--text);
      padding: 6px 8px;
      text-align: left;
      font: inherit;
      cursor: pointer;
    }}
    .folder-button:hover, .folder-button.active {{
      background: #eef4ff;
      color: #174ea6;
    }}
    .folder-name {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .folder-count {{
      color: var(--muted);
      font-size: 12px;
    }}
    .folder-section {{
      margin-bottom: 18px;
    }}
    .folder-heading {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      margin: 0 0 8px;
      padding: 0 2px;
    }}
    .folder-heading h2 {{
      margin: 0;
      font-size: 17px;
      font-weight: 650;
      line-height: 1.35;
    }}
    .mail-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 12px;
      overflow: hidden;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    .mail-head {{
      display: grid;
      grid-template-columns: 58px 130px 1fr auto;
      gap: 12px;
      align-items: start;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}
    .badge {{
      display: inline-flex;
      justify-content: center;
      align-items: center;
      min-width: 42px;
      height: 28px;
      border-radius: 999px;
      color: #fff;
      font-weight: 700;
      font-size: 13px;
    }}
    .badge.sent {{ background: var(--sent); }}
    .badge.received {{ background: var(--received); }}
    .date {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
    .subject {{ font-size: 16px; font-weight: 650; line-height: 1.35; }}
    .meta {{
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }}
    .ai-meta {{
      border-left: 3px solid var(--accent);
      padding: 6px 10px;
      background: #f6f8ff;
      color: #334155;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 6px;
    }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      color: var(--muted);
      background: #f8fafc;
      white-space: nowrap;
    }}
    .chip.review {{ color: var(--warn); border-color: #f0c36a; background: #fff8e6; }}
    .mail-body {{
      padding: 14px 16px 16px 86px;
      line-height: 1.48;
      font-size: 14px;
    }}
    .excerpt {{
      white-space: pre-wrap;
      margin: 0 0 12px;
      color: #253142;
    }}
    .inline-images {{
      display: grid;
      gap: 10px;
      margin: 12px 0;
    }}
    .inline-mail-image {{
      display: block;
      max-width: min(100%, 760px);
      max-height: 520px;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
    }}
    .attachments {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }}
    .attachments a, .attachment-missing {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      font-size: 13px;
      text-decoration: none;
      color: var(--accent);
      background: #f8fafc;
    }}
    .attachment-missing {{ color: var(--muted); }}
    .empty {{
      padding: 32px;
      text-align: center;
      color: var(--muted);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    @media (max-width: 760px) {{
      .toolbar {{ grid-template-columns: 1fr; }}
      .mail-head {{ grid-template-columns: 48px 1fr; }}
      .date {{ grid-column: 2; }}
      .chips {{ grid-column: 1 / -1; justify-content: flex-start; }}
      .mail-body {{ padding-left: 16px; }}
      .project-layout {{ grid-template-columns: 1fr; }}
      .folder-panel {{ position: static; max-height: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <h1>{_e(project_number)} - Correspondance projet</h1>
      <div class="toolbar">
        <input id="search" type="search" placeholder="Rechercher dans les echanges">
        <select id="directionFilter">
          <option value="all">Envoye et recu</option>
          <option value="sent">Envoyes</option>
          <option value="received">Recus</option>
        </select>
        <select id="typeFilter">
          <option value="all">Tous les types</option>
          {_render_options(mail_types)}
        </select>
        <select id="folderFilter">
          <option value="all">Tous les dossiers</option>
          {_render_options(mail_folders)}
        </select>
        <select id="interlocutorFilter">
          <option value="all">Tous les interlocuteurs</option>
          {_render_options(interlocutors)}
        </select>
      </div>
    </div>
  </header>
  <main>
    <div class="summary">
      <span id="visibleCount">{len(entries)}</span> mail(s) affiches sur {len(entries)}
    </div>
    <div class="project-layout">
      <aside class="folder-panel" aria-label="Arborescence des echanges">
        <h2>Arborescence</h2>
        {folder_nav}
      </aside>
      <section id="mailList">
        {folder_sections}
      </section>
    </div>
    <div id="emptyState" class="empty" hidden>Aucun mail ne correspond aux filtres.</div>
  </main>
  <script>
    const cards = Array.from(document.querySelectorAll(".mail-card"));
    const sections = Array.from(document.querySelectorAll(".folder-section"));
    const folderButtons = Array.from(document.querySelectorAll("[data-folder-filter]"));
    const search = document.getElementById("search");
    const directionFilter = document.getElementById("directionFilter");
    const typeFilter = document.getElementById("typeFilter");
    const folderFilter = document.getElementById("folderFilter");
    const interlocutorFilter = document.getElementById("interlocutorFilter");
    const visibleCount = document.getElementById("visibleCount");
    const emptyState = document.getElementById("emptyState");
    let activeFolder = "all";

    function matches(value, filter) {{
      return filter === "all" || value === filter;
    }}

    function matchesFolder(value, filter) {{
      return filter === "all" || value === filter || value.startsWith(filter + "/");
    }}

    function applyFilters() {{
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      for (const card of cards) {{
        const ok =
          matches(card.dataset.direction, directionFilter.value) &&
          matches(card.dataset.type, typeFilter.value) &&
          matchesFolder(card.dataset.folder, folderFilter.value) &&
          matchesFolder(card.dataset.folder, activeFolder) &&
          matches(card.dataset.interlocutor, interlocutorFilter.value) &&
          card.dataset.search.includes(query);
        card.hidden = !ok;
        if (ok) visible += 1;
      }}
      for (const section of sections) {{
        const sectionCards = Array.from(section.querySelectorAll(".mail-card"));
        section.hidden = !sectionCards.some((card) => !card.hidden);
      }}
      visibleCount.textContent = String(visible);
      emptyState.hidden = visible !== 0;
    }}

    for (const button of folderButtons) {{
      button.addEventListener("click", () => {{
        activeFolder = button.dataset.folderFilter;
        for (const item of folderButtons) item.classList.toggle("active", item === button);
        applyFilters();
      }});
    }}

    search.addEventListener("input", applyFilters);
    directionFilter.addEventListener("change", applyFilters);
    typeFilter.addEventListener("change", applyFilters);
    folderFilter.addEventListener("change", applyFilters);
    interlocutorFilter.addEventListener("change", applyFilters);
    applyFilters();
  </script>
</body>
</html>
"""


def _render_folder_sections(entries: list[HtmlMailEntry]) -> str:
    grouped: dict[str, list[HtmlMailEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.row.decision.target_relative_folder, []).append(entry)
    sections = []
    for folder, folder_entries in sorted(
        grouped.items(),
        key=lambda item: folder_sort_key(item[0]),
    ):
        cards = "\n".join(_render_mail_card(entry) for entry in folder_entries)
        sections.append(
            f"""
      <section class="folder-section" data-folder-section="{_e(folder)}">
        <div class="folder-heading">
          <h2>{_e(folder)}</h2>
          <span class="meta">{len(folder_entries)} mail(s)</span>
        </div>
        {cards}
      </section>
"""
        )
    return "\n".join(sections)


def _render_folder_nav(nodes: list[FolderTreeNode], *, total_count: int) -> str:
    root_button = (
        '<button class="folder-button active" type="button" data-folder-filter="all">'
        '<span class="folder-name">Tous les dossiers</span>'
        f'<span class="folder-count">{total_count}</span>'
        "</button>"
    )
    return f'{root_button}<ul class="folder-tree">{_render_folder_nav_nodes(nodes)}</ul>'


def _render_folder_nav_nodes(nodes: list[FolderTreeNode] | tuple[FolderTreeNode, ...]) -> str:
    items = []
    for node in nodes:
        child_html = (
            f"<ul>{_render_folder_nav_nodes(node.children)}</ul>" if node.children else ""
        )
        items.append(
            "<li>"
            '<button class="folder-button" type="button" '
            f'data-folder-filter="{_e(node.relative_folder)}">'
            f'<span class="folder-name">{_e(node.name)}</span>'
            f'<span class="folder-count">{node.mail_count}</span>'
            "</button>"
            f"{child_html}"
            "</li>"
        )
    return "".join(items)


def _render_mail_card(entry: HtmlMailEntry) -> str:
    row = entry.row
    mail = row.mail
    decision = row.decision
    direction_class = "sent" if mail.direction == Direction.SENT else "received"
    direction_label = "E" if mail.direction == Direction.SENT else "R"
    direction_text = "Envoye" if mail.direction == Direction.SENT else "Recu"
    sender = mail.sender_name or mail.sender_email or "-"
    recipients = ", ".join(mail.recipients) if mail.recipients else "-"
    attachments = _render_attachments(entry.attachments)
    inline_images = _render_inline_images(entry.inline_images)
    ai_meta = _render_ai_meta(row)
    search_text = " ".join(
        [
            mail.project_number,
            mail.subject,
            sender,
            recipients,
            " ".join(mail.attachment_names),
            mail.body_excerpt,
            decision.mail_type.value,
            decision.interlocutor.value,
            decision.target_relative_folder,
            decision.reason,
            _ai_search_text(row),
        ]
    ).lower()
    review_class = " review" if row.action.value == "review" or decision.requires_review else ""
    return f"""
      <article class="mail-card"
        data-direction="{_e(mail.direction.value)}"
        data-type="{_e(decision.mail_type.value)}"
        data-folder="{_e(decision.target_relative_folder)}"
        data-interlocutor="{_e(decision.interlocutor.value)}"
        data-search="{_e(search_text)}">
        <div class="mail-head">
          <span class="badge {direction_class}">{_e(str(entry.order))}-{direction_label}</span>
          <div class="date">{mail.sent_at:%Y-%m-%d %H:%M}</div>
          <div>
            <div class="subject">{_e(mail.subject or "Sans sujet")}</div>
            <div class="meta">
              <b>{_e(direction_text)}</b> - {_e(sender)}<br>
              Destinataires: {_e(recipients)}
            </div>
          </div>
          <div class="chips">
            <span class="chip{review_class}">{_e(decision.mail_type.value)}</span>
            <span class="chip">{_e(decision.interlocutor.value)}</span>
            <span class="chip">{_e(decision.target_relative_folder)}</span>
            <span class="chip">{decision.confidence:.0%}</span>
          </div>
        </div>
        <div class="mail-body">
          <p class="excerpt">{_e(mail.body_excerpt or "(Aucun extrait disponible)")}</p>
          {inline_images}
          <div class="meta">Raison: {_e(decision.reason)}</div>
          {ai_meta}
          {attachments}
        </div>
      </article>
"""


def _render_inline_images(inline_images: list[HtmlInlineImage]) -> str:
    if not inline_images:
        return ""
    images = "".join(
        f'<img class="inline-mail-image" src="{_e(image.data_uri)}" alt="{_e(image.name)}">'
        for image in inline_images
    )
    return f'<div class="inline-images">{images}</div>'


def _render_attachments(attachments: list[HtmlAttachmentLink]) -> str:
    if not attachments:
        return ""
    links = []
    for attachment in attachments:
        label = _e(attachment.name)
        if attachment.href is None:
            links.append(f'<span class="attachment-missing">{label} (non exportee)</span>')
        else:
            links.append(
                f'<a href="{_e(attachment.href)}" target="_blank" '
                f'rel="noopener">{label}</a>'
            )
    return f'<div class="attachments">{"".join(links)}</div>'


def _render_ai_meta(row: PreviewRow) -> str:
    ai = row.classification.ai
    if ai is None:
        return ""
    action = "archiver" if ai.archive else "ne pas archiver"
    return (
        '<div class="meta ai-meta">'
        f"<b>Decision IA:</b> {_e(action)} - {_e(ai.mail_type)} - "
        f"{_e(ai.interlocutor)} - {_e(ai.target_folder)} - {ai.confidence:.0%}<br>"
        f"<b>Resume IA:</b> {_e(ai.short_summary)}<br>"
        f"<b>Pourquoi:</b> {_e(ai.reason)}"
        "</div>"
    )


def _ai_search_text(row: PreviewRow) -> str:
    ai = row.classification.ai
    if ai is None:
        return ""
    return " ".join(
        [
            ai.mail_type,
            ai.interlocutor,
            ai.target_folder,
            ai.short_summary,
            ai.reason,
        ]
    )


def _render_options(values: list[str]) -> str:
    return "\n".join(
        f'<option value="{_e(value)}">{_e(value)}</option>'
        for value in values
    )


def _rows_by_project(rows: Sequence[PreviewRow]) -> dict[str, list[PreviewRow]]:
    grouped: dict[str, list[PreviewRow]] = {}
    for row in rows:
        grouped.setdefault(row.mail.project_number, []).append(row)
    return grouped


def _project_html_path(projects_root: Path, project_number: str) -> Path:
    project_path = local_project_path(projects_root, project_number)
    return project_path / "Correspondance" / f"{project_number} - Correspondance projet.html"


def _relative_attachment_href(attachment_dir: Path, attachment_path: Path) -> str:
    return "./" + "/".join(
        [quote(attachment_dir.name, safe=""), quote(attachment_path.name, safe="")]
    )


def _inline_image_from_attachment(
    attachment: Any,
    original_name: str,
) -> HtmlInlineImage | None:
    try:
        with tempfile.TemporaryDirectory(prefix="mailflow-inline-") as temp_dir:
            temp_path = Path(temp_dir) / original_name
            attachment.SaveAsFile(str(temp_path))
            data = temp_path.read_bytes()
    except Exception:
        return None
    if not data:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return HtmlInlineImage(
        name=original_name,
        data_uri=f"data:{attachment_mime_type(attachment)};base64,{encoded}",
    )


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)
