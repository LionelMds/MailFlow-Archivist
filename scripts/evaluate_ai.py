"""Measure AI classification accuracy on mails you already classified.

Reference answers are your manual corrections and your archived mails, read from
MailFlow's local database. Each mail is re-read from Outlook (read only) and
classified again by the chosen engine; the proposal is compared with your answer.

Examples (Windows, from the repository, with Outlook open):
    .venv312\\Scripts\\python.exe scripts/evaluate_ai.py
    .venv312\\Scripts\\python.exe scripts/evaluate_ai.py --run --provider ollama --limit 50
    .venv312\\Scripts\\python.exe scripts/evaluate_ai.py --run --provider openai --limit 50

Without --run nothing is read from Outlook and no AI is called: only the number of
reference mails is shown. With --provider openai, the mails are sent to OpenAI exactly
as during a normal classification, and the calls are billed to your account.
Nothing is archived, moved, renamed or recategorised; the report written with
--output contains identifiers and categories only, never subjects or bodies.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mailflow.config import load_settings
from mailflow.core.app_controller import build_ai_classifier
from mailflow.core.evaluation import (
    CaseResult,
    EvaluationCase,
    evaluate_case,
    format_summary,
    load_reference_cases,
    result_to_json,
    summarize,
)
from mailflow.models import AiMode, MailMetadata
from mailflow.storage.directory_store import SQLiteDirectoryStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--run", action="store_true", help="Lire Outlook et appeler l'IA.")
    parser.add_argument(
        "--provider", choices=("openai", "ollama"),
        help="Moteur a mesurer (par defaut celui des reglages).",
    )
    parser.add_argument("--model", help="Modele a mesurer (par defaut celui des reglages).")
    parser.add_argument(
        "--source", choices=("all", "correction", "archive"), default="all",
        help="Corrections manuelles, mails archives, ou les deux.",
    )
    parser.add_argument("--limit", type=int, help="Nombre maximum de mails (tirage aleatoire).")
    parser.add_argument("--seed", type=int, default=1, help="Graine du tirage, pour comparer.")
    parser.add_argument(
        "--price-input", type=float,
        help="Prix OpenAI par million de tokens en entree, pour estimer le cout.",
    )
    parser.add_argument(
        "--price-output", type=float,
        help="Prix OpenAI par million de tokens en sortie, pour estimer le cout.",
    )
    parser.add_argument(
        "--output", type=Path, nargs="?", const=REPOSITORY_ROOT / "build" / "ai-evaluation.json",
        help="Rapport JSON facultatif; le chemin doit rester dans build/.",
    )
    return parser


def select_cases(
    cases: list[EvaluationCase], *, source: str, limit: int | None, seed: int,
) -> list[EvaluationCase]:
    selected = [case for case in cases if source == "all" or case.source == source]
    selected.sort(key=lambda case: case.entry_id)
    if limit is not None and len(selected) > limit:
        selected = random.Random(seed).sample(selected, limit)
    return selected


def outlook_fetcher(account_email: str) -> Any:
    from mailflow.outlook.client import OutlookClient
    from mailflow.outlook.scanner import OutlookScanner

    namespace = OutlookClient().namespace
    scanner = OutlookScanner(account_email=account_email)
    projects: dict[str, str] = {}

    def fetch(entry_id: str) -> MailMetadata | None:
        try:
            item = namespace.GetItemFromID(entry_id)
            folder = str(getattr(getattr(item, "Parent", None), "Name", ""))
            return scanner.mail_item_to_metadata(
                item, project_number=projects[entry_id], outlook_folder=folder,
            )
        except Exception:
            # Deleted or moved to another store: skip it rather than guess.
            return None

    return fetch, projects


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit doit etre positif.")
    if (args.price_input is None) != (args.price_output is None):
        parser.error("Indiquer --price-input et --price-output ensemble.")
    output = args.output.resolve() if args.output is not None else None
    if output is not None and not output.is_relative_to(REPOSITORY_ROOT / "build"):
        parser.error("Le rapport doit etre enregistre dans le repertoire build du projet.")

    settings = load_settings()
    cases = select_cases(
        load_reference_cases(settings.paths.sqlite_file),
        source=args.source, limit=args.limit, seed=args.seed,
    )
    counts = Counter((case.source, case.expected_category.value) for case in cases)
    print(f"{len(cases)} mail(s) de reference selectionne(s) :")
    for (source, category), count in sorted(counts.items()):
        print(f"  {source:<10} {category:<16} {count}")
    if not cases:
        print("Corriger ou archiver des mails dans MailFlow pour constituer une reference.")
        return 0
    if not args.run:
        print("Aucun appel IA. Ajouter --run pour mesurer (Outlook doit etre ouvert).")
        return 0

    updates: dict[str, Any] = {"ai_mode": AiMode.ALL}
    if args.provider:
        updates["ai_provider"] = args.provider
    provider = args.provider or settings.ai_provider
    if args.model:
        updates["ollama_model" if provider == "ollama" else "ai_model"] = args.model
    settings = settings.model_copy(update=updates)
    classifier = build_ai_classifier(settings)
    if classifier is None:
        print("Moteur IA indisponible : verifier la cle OpenAI dans les reglages.")
        return 2
    model = settings.ollama_model if provider == "ollama" else settings.ai_model
    engine = f"{provider} / {model}"
    if provider == "openai":
        print(f"Les {len(cases)} mails vont etre envoyes a OpenAI ({model}); appels factures.")

    fetch, projects = outlook_fetcher(settings.selected_outlook_account or "")
    projects.update({case.entry_id: case.project_number for case in cases})
    directory = SQLiteDirectoryStore(settings.paths.sqlite_file)
    results: list[CaseResult] = []
    interrupted = False
    try:
        for number, case in enumerate(cases, start=1):
            result = evaluate_case(
                case,
                fetch_mail=fetch,
                classifier=classifier,
                directory=directory,
                include_body=settings.ai_include_body_excerpt,
                privacy_mask_phone_numbers=settings.privacy_mask_phone_numbers,
                confidence_threshold=settings.decision_confidence_threshold,
            )
            results.append(result)
            if result.missing_in_outlook:
                status = "introuvable"
            elif result.inconsistent_reference:
                status = "ecarte : role de l'annuaire different de la reference"
            elif result.error:
                status = f"erreur {result.error}"
            else:
                verdict = "OK " if result.correct else "FAUX"
                review = " (a verifier)" if result.requires_review else ""
                status = (
                    f"{verdict} attendu {case.expected_category.value}, "
                    f"propose {result.predicted_category}{review}"
                )
            print(f"[{number}/{len(cases)}] {case.project_number} {status}", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print("Interrompu : resultat partiel.")

    summary = summarize(results, planned=len(cases))
    prices = (
        (args.price_input, args.price_output) if args.price_input is not None else None
    )
    print()
    print(format_summary(summary, engine=engine, price_per_million=prices))
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "completed_at": datetime.now(UTC).isoformat(),
            "engine": engine,
            "interrupted": interrupted,
            "accuracy": summary.accuracy,
            "summary": {
                key: value for key, value in vars(summary).items() if key != "confusion"
            },
            "confusion": summary.confusion,
            "results": [result_to_json(result) for result in results],
        }
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        print(f"Rapport : {output}")
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
