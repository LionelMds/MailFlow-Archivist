"""Validate a local model with synthetic French mail, explicitly using --run.

Examples (from an environment with the project installed):
    python scripts/validate_ollama.py
    python scripts/validate_ollama.py --run --output
    python scripts/validate_ollama.py --run --model qwen3.5:4b --repeat 2

Without --run this only validates the fixtures and never contacts Ollama. This
script is intentionally outside pytest, so regular CI never needs a local model.
No Outlook account, real mail, application settings or archive is read or changed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mailflow.classifier.ollama_classifier import OllamaClassifier
from mailflow.classifier.routing_context import ResolvedCounterparty, build_routing_context
from mailflow.config import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
)
from mailflow.models import InterlocutorType, MailMetadata, RoutingCategory

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "ollama_synthetic_mail_cases.json"


class CounterpartyFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    organization_name: str
    role: InterlocutorType
    organization_locked: bool
    source: str


class ExpectedClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[RoutingCategory] = Field(min_length=1)
    role: InterlocutorType
    requires_review: bool


class SyntheticCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    mail: MailMetadata
    counterparty: CounterpartyFixture
    history: list[dict[str, str]]
    expected: ExpectedClassification


def load_cases() -> list[SyntheticCase]:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if raw.get("synthetic") is not True:
        raise ValueError("Le jeu de messages doit être explicitement synthétique.")
    cases = [SyntheticCase.model_validate(case) for case in raw["cases"]]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Les identifiants des cas doivent être uniques et non vides.")
    return cases


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("La valeur doit être strictement positive.")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Exécuter les appels réels à Ollama.")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--timeout", type=_positive_float, default=DEFAULT_OLLAMA_TIMEOUT_SECONDS)
    parser.add_argument("--repeat", type=int, choices=range(1, 11), default=1)
    parser.add_argument(
        "--case", action="append", dest="case_ids", help="Identifiant à sélectionner."
    )
    parser.add_argument("--fail-fast", action="store_true", help="Arrêter au premier échec.")
    parser.add_argument(
        "--output",
        type=Path,
        nargs="?",
        const=REPOSITORY_ROOT / "build" / "ollama-validation.json",
        help="Rapport JSON facultatif; le chemin doit rester dans build/.",
    )
    return parser


def evaluate_case(classifier: OllamaClassifier, case: SyntheticCase) -> dict[str, Any]:
    counterparty = ResolvedCounterparty(**case.counterparty.model_dump())
    context = build_routing_context(
        case.mail, counterparty, history=case.history, verified_examples=[]
    )
    started = time.perf_counter()
    result: dict[str, Any] = {
        "id": case.id,
        "description": case.description,
        "expected": case.expected.model_dump(mode="json"),
    }
    mismatches = []
    try:
        actual = classifier.classify(case.mail, known_context=context)
        result["actual"] = actual.model_dump(mode="json")
        if actual.category not in case.expected.categories:
            mismatches.append(f"catégorie inattendue: {actual.category}")
        if actual.organization_role != case.expected.role:
            mismatches.append(f"rôle inattendu: {actual.organization_role}")
        if actual.requires_review != case.expected.requires_review:
            mismatches.append(f"vérification inattendue: {actual.requires_review}")
        if (
            case.counterparty.organization_locked
            and actual.organization_name not in (None, case.counterparty.organization_name)
        ):
            mismatches.append(f"entreprise de l'annuaire contredite: {actual.organization_name}")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        mismatches.append("classification impossible")
    result["seconds"] = round(time.perf_counter() - started, 3)
    result["passed"] = not mismatches
    result["mismatches"] = mismatches
    return result


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
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
        print(f"{len(cases)} cas synthétiques valides. Aucun appel à Ollama.")
        for case in cases:
            print(f"  {case.id}: {case.description}")
        print("Ajouter --run pour tester le modèle, puis --output pour conserver un rapport JSON.")
        return 0

    classifier = OllamaClassifier(
        base_url=args.base_url, model=args.model, timeout_seconds=args.timeout
    )
    print(f"Validation réelle: {args.model}, {len(cases)} cas x {args.repeat} répétition(s).")
    print("Le premier appel peut inclure le chargement du modèle.", flush=True)
    results = []
    interrupted = False
    try:
        for repetition in range(1, args.repeat + 1):
            for case in cases:
                result = evaluate_case(classifier, case)
                result["repetition"] = repetition
                results.append(result)
                status = "OK" if result["passed"] else "ÉCHEC"
                actual = result.get("actual", {})
                print(
                    f"[{status}] {case.id} ({result['seconds']:.1f} s) "
                    f"{actual.get('category', '-')} / {actual.get('organization_role', '-')} / "
                    f"à vérifier={actual.get('requires_review', '-')}",
                    flush=True,
                )
                for mismatch in result["mismatches"]:
                    print(f"  {mismatch}", flush=True)
                if "error" in result:
                    print(f"  {result['error']}", flush=True)
                if args.fail_fast and not result["passed"]:
                    break
            if args.fail_fast and not results[-1]["passed"]:
                break
    except KeyboardInterrupt:
        interrupted = True

    durations = [result["seconds"] for result in results]
    passed = sum(result["passed"] for result in results)
    failed = len(results) - passed
    report = {
        "synthetic_data_only": True,
        "completed_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "base_url": args.base_url,
        "timeout_seconds": args.timeout,
        "fixture": str(FIXTURE_PATH.relative_to(REPOSITORY_ROOT)),
        "planned_cases": len(cases) * args.repeat,
        "completed_cases": len(results),
        "passed": passed,
        "failed": failed,
        "interrupted": interrupted,
        "total_seconds": round(sum(durations), 3),
        "median_seconds": round(statistics.median(durations), 3) if durations else None,
        "first_case_seconds": durations[0] if durations else None,
        "results": results,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Rapport: {output}")
    print(f"Résultat: {passed}/{len(results)} réussis, {failed} échec(s).")
    if interrupted:
        return 130
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
