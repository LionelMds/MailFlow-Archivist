from __future__ import annotations

import argparse

from mailflow.config import load_settings
from mailflow.core.app_controller import build_default_controller
from mailflow.core.contact_directory import DirectoryImportResult
from mailflow.diagnostics import collect_outlook_diagnostics, format_outlook_diagnostics
from mailflow.logging import configure_logging
from mailflow.ui.main_window import run_desktop_app


def main() -> int:
    parser = argparse.ArgumentParser(prog="mailflow-archivist")
    parser.add_argument(
        "--diagnose-outlook",
        action="store_true",
        help="Liste les comptes et dossiers racine Outlook sans modifier de donnees.",
    )
    parser.add_argument(
        "--import-contact-directory",
        action="store_true",
        help="Scanne tous les dossiers projet Outlook et alimente l'annuaire local.",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="Compte Outlook a utiliser pour l'import annuaire.",
    )
    parser.add_argument(
        "--outlook-root",
        default=None,
        help="Dossier Outlook racine a scanner pour l'import annuaire.",
    )
    args = parser.parse_args()
    settings = load_settings()
    configure_logging(settings.paths.log_dir)
    if args.diagnose_outlook:
        print(format_outlook_diagnostics(collect_outlook_diagnostics()))
        return 0
    if args.import_contact_directory:
        controller = build_default_controller(settings)
        result = controller.import_contact_directory(
            account_identifier=args.account or settings.selected_outlook_account,
            outlook_root_folder=args.outlook_root or settings.outlook_root_folder,
        )
        print(format_directory_import_result(result))
        return 0
    return run_desktop_app(settings)


def format_directory_import_result(result: DirectoryImportResult) -> str:
    return "\n".join(
        [
            "Import annuaire Outlook termine:",
            f"- mails scannes: {result.scanned_mail_count}",
            f"- contacts externes observes: {result.observed_contact_count}",
            f"- contacts importes: {result.imported_contact_count}",
            f"- organisations creees: {result.new_organizations}",
            f"- domaines ajoutes: {result.new_domains}",
            f"- contacts ajoutes: {result.new_contacts}",
            f"- participations projet ajoutees: {result.new_project_participants}",
            f"- internes ignores: {result.skipped_internal_count}",
            f"- domaines generiques ignores: {result.skipped_generic_domain_count}",
        ]
    )
