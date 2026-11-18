from __future__ import annotations

import html

from mailflow.core.project_digest import ProjectDigest


def project_digest_to_html(digest: ProjectDigest) -> str:
    if not digest.has_rows:
        return (
            "<div style='font-family: Barlow, Segoe UI, Arial, sans-serif; font-size: 14px; "
            "color:#6b6c6e;'>"
            "Aucun projet scanne pour le moment."
            "</div>"
        )
    return (
        "<div style='font-family: Barlow, Segoe UI, Arial, sans-serif; font-size: 14px;'>"
        "<h3 style='margin:0 0 8px; font-family: Barlow Condensed, Segoe UI; "
        f"font-size: 20px;'>Resume projet {_e(digest.project_number)}</h3>"
        f"{_stats_html(digest)}"
        f"{_section_html('Global', digest.global_points)}"
        f"{_section_html('Clients', digest.client_points)}"
        f"{_section_html('Fournisseurs', digest.supplier_points)}"
        f"{_section_html('Commandes', digest.order_points)}"
        f"{_section_html('Problemes / reclamations', digest.issue_points)}"
        "</div>"
    )


def _stats_html(digest: ProjectDigest) -> str:
    dates = "-"
    if digest.first_date is not None and digest.last_date is not None:
        dates = f"{digest.first_date:%d.%m.%Y} - {digest.last_date:%d.%m.%Y}"
    return (
        "<p style='margin:0 0 8px; color:#6b6c6e;'>"
        f"{digest.mail_count} mail(s), {digest.sent_count} envoye(s), "
        f"{digest.received_count} recu(s) | {dates}"
        "</p>"
    )


def _section_html(title: str, points: tuple[str, ...]) -> str:
    if not points:
        return ""
    items = "".join(f"<li>{_e(point)}</li>" for point in points)
    return (
        "<section style='border-top:1px solid #e1e1e2; padding-top:7px; margin-top:7px;'>"
        f"<h4 style='margin:0 0 4px; font-size:10pt;'>{_e(title)}</h4>"
        f"<ul style='margin:0; padding-left:18px;'>{items}</ul>"
        "</section>"
    )


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)
