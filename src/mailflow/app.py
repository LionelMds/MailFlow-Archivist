from __future__ import annotations

import argparse

from mailflow.config import load_settings
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
    args = parser.parse_args()
    settings = load_settings()
    configure_logging(settings.paths.log_dir)
    if args.diagnose_outlook:
        print(format_outlook_diagnostics(collect_outlook_diagnostics()))
        return 0
    return run_desktop_app(settings)
