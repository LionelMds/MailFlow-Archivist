"""Validate Jev with the synthetic French mails, explicitly using --run.

Examples (from the repository, after saving the Jev key in MailFlow's settings):
    .venv312\\Scripts\\python.exe scripts/validate_jev.py
    .venv312\\Scripts\\python.exe scripts/validate_jev.py --run --output

Without --run this only validates the fixtures and never contacts TypeSafe. With
--run, only the ten synthetic mails of tests/fixtures are sent to the Jev API, using
the key stored in the system keyring; the calls are billed to the TypeSafe account.
No Outlook account, real mail, archive or setting is read or changed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from validate_ollama import FIXTURE_PATH, REPOSITORY_ROOT, evaluate_case, load_cases

from mailflow.classifier.jev_classifier import JevClassifier
from mailflow.config import DEFAULT_JEV_MODEL, DEFAULT_JEV_TIMEOUT_SECONDS, get_jev_api_key


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Exécuter les appels réels à Jev.")
    parser.add_argument("--model", default=DEFAULT_JEV_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_JEV_TIMEOUT_SECONDS)
    parser.add_argument(
        "--case", action="append", dest="case_ids", help="Identifiant à sélectionner."
    )
    parser.add_argument(
        "--output",
        type=Path,
        nargs="?",
        const=REPOSITORY_ROOT / "build" / "jev-validation.json",
        help="Rapport JSON facultatif; le chemin doit rester dans build/.",
    )
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout doit être strictement positif.")
    cases = load_cases()
    if args.case_ids:
        unknown_ids = set(args.case_ids) - {case.id for case in cases}
        if unknown_ids:
            parser.error(f"Cas inconnu(s): {', '.join(sorted(unknown_ids))}")
        cases = [case for case in cases if case.id in args.case_ids]
    output = args.output.resolve() if args.output is not None else None
    if output is not None and not output.is_relative_to(REPOSITORY_ROOT / "build"):
        parser.error("Le rapport doit être enregistré dans le répertoire build du projet.")
    if not args.run:
        print(f"{len(cases)} cas synthétiques valides. Aucun appel à Jev.")
        print("Ajouter --run pour tester Jev, puis --output pour conserver un rapport JSON.")
        return 0
    api_key = get_jev_api_key()
    if not api_key:
        print("Aucune clé Jev : l'enregistrer d'abord dans les réglages de MailFlow.")
        return 2

    classifier = JevClassifier(api_key=api_key, model=args.model, timeout_seconds=args.timeout)
    print(f"Validation Jev : {args.model}, {len(cases)} cas synthétiques.", flush=True)
    results = []
    tokens = 0
    for case in cases:
        result = evaluate_case(classifier, case)
        result["served_model"] = classifier.last_served_model
        tokens += (classifier.last_usage or (0, 0))[0]
        results.append(result)
        actual = result.get("actual", {})
        status = "OK" if result["passed"] else "ÉCHEC"
        print(
            f"[{status}] {case.id} ({result['seconds']:.2f} s) "
            f"{actual.get('category', '-')} {actual.get('confidence', 0):.0%} / "
            f"à vérifier={actual.get('requires_review', '-')}",
            flush=True,
        )
        if actual.get("reason"):
            print(f"  {actual['reason']}", flush=True)
        for mismatch in result["mismatches"]:
            print(f"  {mismatch}", flush=True)
        if "error" in result:
            print(f"  {result['error']}", flush=True)

    durations = [result["seconds"] for result in results]
    passed = sum(result["passed"] for result in results)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "synthetic_data_only": True,
            "completed_at": datetime.now(UTC).isoformat(),
            "model": args.model,
            "fixture": str(FIXTURE_PATH.relative_to(REPOSITORY_ROOT)),
            "passed": passed,
            "failed": len(results) - passed,
            "input_tokens": tokens,
            "median_seconds": round(statistics.median(durations), 3) if durations else None,
            "results": results,
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Rapport: {output}")
    print(f"Résultat: {passed}/{len(results)} réussis, {tokens} tokens en entrée.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
