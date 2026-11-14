"""Оценка качества ассистента на размеченных вопросах.

    python scripts/evaluate.py                    # движок правил
    python scripts/evaluate.py --engine claude    # Claude: нужен ANTHROPIC_API_KEY, запросы платные

Наборы:
  dev      26 вопросов, на которых настраивались синонимы и порог поиска;
  holdout  18 вопросов в разговорных и перефразированных формулировках, в настройке не участвовали.

Главные показатели:
  верное действие   ассистент ответил, уточнил или передал оператору там, где должен был;
  верный источник   среди вопросов с ответом в базе — ответ опирается на нужный раздел;
  ложные ответы     ассистент ответил там, где должен был позвать оператора (самая опасная ошибка).
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.assistant import Assistant  # noqa: E402
from app.config import KB_DIR, get_settings, load_company, load_json  # noqa: E402
from app.engines import build_engine  # noqa: E402
from app.engines.rules import RulesEngine  # noqa: E402
from app.kb import load_kb  # noqa: E402
from app.orders import Orders  # noqa: E402


def evaluate(assistant: Assistant, items: list[dict]) -> dict:
    action_hits = source_hits = answerable = false_answers = extra_escalations = 0
    cost = latency = 0.0
    errors: list[dict] = []
    for item in items:
        reply = assistant.respond([], item["question"])
        expected, draft = item["expected"], reply.draft
        cost += reply.meta.cost_usd or 0
        latency += reply.meta.latency_ms
        action_ok = draft.action == expected["action"]
        action_hits += action_ok
        source_ok = True
        if "sources" in expected:
            answerable += 1
            source_ok = action_ok and bool(set(draft.sources) & set(expected["sources"]))
            source_hits += source_ok
        if "order" in expected:
            source_ok = bool(reply.order and reply.order.number == expected["order"] and reply.order.found)
        if expected["action"] == "escalate" and draft.action == "answer":
            false_answers += 1
        if expected["action"] != "escalate" and draft.action == "escalate":
            extra_escalations += 1
        if not (action_ok and source_ok):
            errors.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "expected": expected,
                    "got": {"action": draft.action, "sources": draft.sources},
                }
            )
    total = len(items)
    return {
        "total": total,
        "action_accuracy": round(action_hits / total, 3),
        "source_accuracy": round(source_hits / answerable, 3) if answerable else None,
        "answerable": answerable,
        "false_answers": false_answers,
        "extra_escalations": extra_escalations,
        "avg_latency_ms": round(latency / total, 1),
        "cost_usd": round(cost, 4),
        "errors": errors,
    }


def print_report(name: str, report: dict) -> None:
    total = report["total"]
    print(f"\n=== {name}: {total} вопросов ===")
    print(f"  Верное действие    {report['action_accuracy'] * 100:5.1f}%  ({round(report['action_accuracy'] * total)}/{total})")
    if report["source_accuracy"] is not None:
        hits = round(report["source_accuracy"] * report["answerable"])
        print(f"  Верный источник    {report['source_accuracy'] * 100:5.1f}%  ({hits}/{report['answerable']})")
    print(f"  Ложные ответы      {report['false_answers']}")
    print(f"  Лишние передачи    {report['extra_escalations']}")
    print(f"  Среднее время: {report['avg_latency_ms']} мс · стоимость: ${report['cost_usd']}")
    for error in report["errors"]:
        expected = error["expected"].get("sources") or error["expected"]["action"]
        got = error["got"]["sources"] or error["got"]["action"]
        print(f"    {error['id']}: «{error['question']}» → ожидалось {expected}, получено {got}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Оценка качества ассистента поддержки")
    parser.add_argument("--engine", choices=["rules", "claude"], default="rules")
    parser.add_argument("--set", choices=["dev", "holdout", "all"], default="all", dest="dataset")
    parser.add_argument("--json", type=Path, help="сохранить отчёт в JSON")
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждение платных запросов")
    args = parser.parse_args()

    settings = get_settings()
    company = load_company()
    sections = load_kb(KB_DIR)
    rules = RulesEngine(company, sections)
    engine = rules if args.engine == "rules" else build_engine("claude", company, sections, settings.model, settings.effort)
    assistant = Assistant(engine, rules, Orders(), sections)

    names = ["dev", "holdout"] if args.dataset == "all" else [args.dataset]
    datasets = {name: load_json(f"eval_{name}.json") for name in names}
    if args.engine == "claude" and not args.yes:
        count = sum(len(items) for items in datasets.values())
        answer = input(f"Будет отправлено {count} платных запросов к {settings.model}. Продолжить? [y/N] ")
        if answer.strip().lower() not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return

    label = "правила" if args.engine == "rules" else f"Claude ({settings.model}, effort={settings.effort})"
    print(f"Движок: {label} · разделов в базе знаний: {len(sections)}")
    started = time.perf_counter()
    reports = {name: evaluate(assistant, items) for name, items in datasets.items()}
    for name, report in reports.items():
        print_report(name, report)
    print(f"\nГотово за {time.perf_counter() - started:.1f} с")

    if args.json:
        args.json.write_text(json.dumps({"engine": label, "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Отчёт сохранён: {args.json}")


if __name__ == "__main__":
    main()
