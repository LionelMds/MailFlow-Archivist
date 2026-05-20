from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from mailflow.core.filenames import build_archive_stem, build_attachment_filename
from mailflow.core.project_paths import local_project_path
from mailflow.models import Direction, PreviewRow
from mailflow.outlook.scanner import iter_com_collection


@dataclass(frozen=True)
class HtmlAttachmentLink:
    name: str
    href: str | None
    path: Path | None


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
        links = _export_attachment_links(
            row=row,
            item=item,
            attachment_dir=attachment_dir,
            mail_stem=mail_stem,
        )
        attachment_paths.extend([link.path for link in links if link.path is not None])
        entries.append(HtmlMailEntry(order=order, row=row, attachments=links))

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
) -> list[HtmlAttachmentLink]:
    if item is None:
        return [
            HtmlAttachmentLink(name=name, href=None, path=None)
            for name in row.mail.attachment_names
        ]

    attachments = iter_com_collection(getattr(item, "Attachments", []))
    if not attachments:
        return [
            HtmlAttachmentLink(name=name, href=None, path=None)
            for name in row.mail.attachment_names
        ]

    attachment_dir.mkdir(parents=True, exist_ok=True)
    links: list[HtmlAttachmentLink] = []
    for attachment in attachments:
        original_name = str(
            getattr(attachment, "FileName", None)
            or getattr(attachment, "DisplayName", None)
            or "piece_jointe"
        )
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
    return links


def _render_project_html(project_number: str, entries: list[HtmlMailEntry]) -> str:
    mail_types = sorted({entry.row.decision.mail_type.value for entry in entries})
    interlocutors = sorted({entry.row.decision.interlocutor.value for entry in entries})
    cards = "\n".join(_render_mail_card(entry) for entry in entries)
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
      grid-template-columns: minmax(220px, 1fr) 150px 220px 180px;
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
      max-width: 1180px;
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
    <section id="mailList">
      {cards}
    </section>
    <div id="emptyState" class="empty" hidden>Aucun mail ne correspond aux filtres.</div>
  </main>
  <script>
    const cards = Array.from(document.querySelectorAll(".mail-card"));
    const search = document.getElementById("search");
    const directionFilter = document.getElementById("directionFilter");
    const typeFilter = document.getElementById("typeFilter");
    const interlocutorFilter = document.getElementById("interlocutorFilter");
    const visibleCount = document.getElementById("visibleCount");
    const emptyState = document.getElementById("emptyState");

    function matches(value, filter) {{
      return filter === "all" || value === filter;
    }}

    function applyFilters() {{
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      for (const card of cards) {{
        const ok =
          matches(card.dataset.direction, directionFilter.value) &&
          matches(card.dataset.type, typeFilter.value) &&
          matches(card.dataset.interlocutor, interlocutorFilter.value) &&
          card.dataset.search.includes(query);
        card.hidden = !ok;
        if (ok) visible += 1;
      }}
      visibleCount.textContent = String(visible);
      emptyState.hidden = visible !== 0;
    }}

    search.addEventListener("input", applyFilters);
    directionFilter.addEventListener("change", applyFilters);
    typeFilter.addEventListener("change", applyFilters);
    interlocutorFilter.addEventListener("change", applyFilters);
  </script>
</body>
</html>
"""


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
            decision.reason,
        ]
    ).lower()
    review_class = " review" if row.action.value == "review" or decision.requires_review else ""
    return f"""
      <article class="mail-card"
        data-direction="{_e(mail.direction.value)}"
        data-type="{_e(decision.mail_type.value)}"
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
            <span class="chip">{decision.confidence:.0%}</span>
          </div>
        </div>
        <div class="mail-body">
          <p class="excerpt">{_e(mail.body_excerpt or "(Aucun extrait disponible)")}</p>
          <div class="meta">Raison: {_e(decision.reason)}</div>
          {attachments}
        </div>
      </article>
"""


def _render_attachments(attachments: list[HtmlAttachmentLink]) -> str:
    if not attachments:
        return ""
    links = []
    for attachment in attachments:
        label = _e(attachment.name)
        if attachment.href is None:
            links.append(f'<span class="attachment-missing">{label} (non exportee)</span>')
        else:
            links.append(f'<a href="{_e(attachment.href)}">{label}</a>')
    return f'<div class="attachments">{"".join(links)}</div>'


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
    return "/".join([quote(attachment_dir.name), quote(attachment_path.name)])


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)
