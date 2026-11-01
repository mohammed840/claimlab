#!/usr/bin/env python3
"""Export completed ClaimLab decisions as deterministic Harbor replay tasks."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_RESULTS = ROOT / "public" / "data" / "results.json"
DEFAULT_TASKS = ROOT / "harbor_tasks"
DEFAULT_DECISIONS = ROOT / "public" / "data" / "decisions.json"


def _decision(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    verdict = value.get("verdict")
    clause_ids = value.get("clause_ids")
    if not isinstance(verdict, str) or not verdict:
        raise ValueError(f"{label}.verdict must be a non-empty string")
    if not isinstance(clause_ids, list) or not all(
        isinstance(clause_id, str) for clause_id in clause_ids
    ):
        raise ValueError(f"{label}.clause_ids must be a list of strings")
    return {"verdict": verdict, "clause_ids": clause_ids}


def load_cases(path: Path, decisions_path: Path = DEFAULT_DECISIONS) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Results file not found: {path}. Run the ClaimLab benchmark first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    cases = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("Expected results JSON with nonempty `tasks`")

    comparisons = json.loads(decisions_path.read_text(encoding="utf-8"))
    if comparisons.get("model") != "gpt-6-luna" or comparisons.get("endpoint") != "/v1/decisions":
        raise ValueError("Expected GPT-6 Luna Decisions API results")
    entries = comparisons.get("tasks", [])
    by_id = {entry["id"]: entry for entry in entries}
    if len(entries) != len(cases) or len(by_id) != len(cases):
        raise ValueError("Expected one unique Decisions API result per case")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"tasks[{index - 1}] must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"tasks[{index - 1}].id must be a non-empty string")
        slug = re.sub(r"[^a-z0-9]+", "-", case_id.lower()).strip("-")
        if not slug or slug in seen:
            raise ValueError(f"Task IDs must produce unique safe names; invalid ID: {case_id!r}")
        seen.add(slug)
        entry = by_id.get(case_id)
        if not entry or entry.get("status") != "complete":
            raise ValueError(f"Missing completed Decisions API verdict for {case_id}")
        transcript = [turn["text"] for turn in case.get("transcript", []) if turn.get("phase") != "decision"]
        if case.get('document_retrieval_required') and (not case.get('policy_retrieval') or entry.get('source_policy_retrieval') != case.get('policy_retrieval')):
            raise ValueError(f"Decisions API result for {case_id} uses stale evidence: document retrieval")
        if entry.get('source_damage_annotations') != (case.get('damage_annotations') if case.get('image') else None):
            raise ValueError(f"Decisions API result for {case_id} uses stale evidence: Astra visual observations changed")
        if entry.get("source_policy") != case.get("policy") or entry.get("source_transcript") != transcript or entry.get("source_image") != case.get("image"):
            raise ValueError(f"Decisions API result for {case_id} uses stale evidence")
        verdict = entry.get("verdict")
        if verdict not in {"covered", "excluded", "needs_review"}:
            raise ValueError(f"Invalid Decisions API verdict for {case_id}")
        decision = {"verdict": verdict}
        expected = _decision(case.get("expected"), f"tasks[{index - 1}].expected")
        policy = case.get("policy")
        if not isinstance(policy, list) or not all(
            isinstance(clause, dict) and isinstance(clause.get("id"), str)
            for clause in policy
        ):
            raise ValueError(f"tasks[{index - 1}].policy must be a list of clauses with IDs")
        valid_clause_ids = [clause["id"] for clause in policy]
        normalized.append(
            {
                "slug": slug,
                "id": case_id,
                "title": case.get("title") if isinstance(case.get("title"), str) else case_id,
                "decision": decision,
                "expected": expected,
                "valid_clause_ids": valid_clause_ids,
            }
        )
    return normalized


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def export_tasks(cases: list[dict[str, Any]], tasks_dir: Path) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        task_dir = tasks_dir / case["slug"]
        for subdir in ("environment", "solution", "tests"):
            (task_dir / subdir).mkdir(parents=True, exist_ok=True)

        description = str(case["title"]).replace('"', "'")
        (task_dir / "task.toml").write_text(
            "\n".join(
                [
                    'schema_version = "1.4"',
                    "",
                    "[task]",
                    f'name = "claimlab/replay-{case["slug"]}"',
                    f'description = "Replay and verify the completed decision for {description}."',
                    "",
                    "[metadata]",
                    'difficulty = "easy"',
                    'category = "evaluation"',
                    'tags = ["claimlab", "decision-replay"]',
                    "",
                    "[agent]",
                    "timeout_sec = 60.0",
                    "",
                    "[verifier]",
                    "timeout_sec = 30.0",
                    "",
                    "[environment]",
                    'network_mode = "no-network"',
                    'workdir = "/workspace"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (task_dir / "environment" / "Dockerfile").write_text(
            "FROM python:3.12-slim\n"
            "WORKDIR /workspace\n"
            "COPY replay.json /workspace/replay.json\n",
            encoding="utf-8",
        )
        _write_json(task_dir / "environment" / "replay.json", {"decision": case["decision"]})
        _write_json(
            task_dir / "tests" / "expected.json",
            {"verdict": case["expected"]["verdict"]},
        )
        (task_dir / "instruction.md").write_text(
            f"# {case['title']}\n\n"
            "Replay the stored completed decision from `/workspace/replay.json` to "
            "`/workspace/answer.json`, preserving its `decision.verdict` value. This task is a deterministic replay of an "
            "existing result, not a new model inference.\n",
            encoding="utf-8",
        )
        (task_dir / "solution" / "solve.sh").write_text(
            "#!/bin/sh\nset -eu\ncp /workspace/replay.json /workspace/answer.json\n",
            encoding="utf-8",
        )
        (task_dir / "solution" / "solve.sh").chmod(0o755)
        (task_dir / "tests" / "test.sh").write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "if python /tests/verify.py /workspace/answer.json /tests/expected.json; then\n"
            "  printf '1.0\\n' > /logs/verifier/reward.txt\n"
            "else\n"
            "  printf '0.0\\n' > /logs/verifier/reward.txt\n"
            "fi\n",
            encoding="utf-8",
        )
        (task_dir / "tests" / "test.sh").chmod(0o755)
        (task_dir / "tests" / "verify.py").write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n\n"
            "answer = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
            "expected = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
            "actual_decision = answer.get('decision') if isinstance(answer, dict) else None\n"
            "verdict = actual_decision.get('verdict') if isinstance(actual_decision, dict) else None\n"
            "if verdict != expected['verdict']:\n"
            "    print(f'verdict={verdict!r}; expected={expected[\"verdict\"]!r}', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "print('Luna Decisions API verdict matches expected label')\n",
            encoding="utf-8",
        )


def _read_rewards(job_dir: Path, cases: list[dict[str, Any]]) -> dict[str, float]:
    rewards: dict[str, float] = {}
    for result_path in job_dir.glob("*/result.json"):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        identity = (result.get('task_name') or result_path.parent.name.split('__', 1)[0]).lower().removeprefix('claimlab/replay-')
        for case in cases:
            if case["slug"] != identity:
                continue
            verifier = result.get("verifier_result") or {}
            result_rewards = verifier.get("rewards") or {}
            reward = result_rewards.get("reward")
            if isinstance(reward, (int, float)):
                rewards[case["id"]] = float(reward)
            break
    return rewards


def _write_summary(cases: list[dict[str, Any]], job_dir: Path, error: str | None = None) -> dict[str, Any]:
    reward_map = _read_rewards(job_dir, cases)
    task_scores = [
        {"id": case["id"], "reward": reward_map.get(case["id"], 0.0), "verdict": case["decision"]["verdict"], "expected": case["expected"]["verdict"]}
        for case in cases
    ]
    passed = sum(score["reward"] >= 1.0 for score in task_scores)
    missing = [score["id"] for score in task_scores if score["id"] not in reward_map]
    if missing and error is None:
        error = "Harbor result missing for: " + ", ".join(missing)
    summary = {
        "status": "completed" if error is None else "failed",
        "mode": "Luna Decisions API saved-verdict replay",
        "model": "gpt-6-luna",
        "endpoint": "/v1/decisions",
        "note": "Scores saved Luna Decisions API verdicts against expected labels; it does not run inference or score Astra explanations.",
        "passed": passed,
        "total": len(cases),
        "score": passed / len(cases) if cases else 0,
        "tasks": task_scores,
        "error": error,
    }
    _write_json(ROOT / "public" / "data" / "harbor-summary.json", summary)
    return summary


def _write_running_summary(cases: list[dict[str, Any]]) -> None:
    _write_json(
        ROOT / "public" / "data" / "harbor-summary.json",
        {
            "status": "running",
            "mode": "Luna Decisions API saved-verdict replay",
            "model": "gpt-6-luna",
            "endpoint": "/v1/decisions",
            "note": "Scores saved Luna Decisions API verdicts against expected labels; it does not run inference or score Astra explanations.",
            "passed": 0,
            "total": len(cases),
            "score": 0.0,
            "tasks": [{"id": case["id"], "reward": None} for case in cases],
            "error": None,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS, help="ClaimLab results JSON")
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS, help="Saved Decisions API results")
    parser.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS, help="Harbor task output directory")
    parser.add_argument("--build-only", action="store_true", help="Export tasks without running Harbor")
    parser.add_argument("--score-only", action="store_true", help="Validate and score saved verdicts locally, without Docker or API calls")
    args = parser.parse_args()

    try:
        cases = load_cases(args.results, args.decisions)
        if args.score_only:
            passed = 0
            for case in cases:
                matched = case["decision"]["verdict"] == case["expected"]["verdict"]
                passed += matched
                print(f"{'PASS' if matched else 'FAIL'} {case['id']}: expected={case['expected']['verdict']} actual={case['decision']['verdict']}")
            print(f"Saved-verdict accuracy: {passed}/{len(cases)} ({passed / len(cases):.1%})")
            return 0
        export_tasks(cases, args.tasks_dir)
    except (OSError, ValueError) as exc:
        print(f"eval_harbor: {exc}", file=sys.stderr)
        return 2

    print(f"Exported {len(cases)} replay tasks to {args.tasks_dir}")
    if args.build_only:
        return 0

    harbor = shutil.which("harbor")
    if harbor is None:
        print("eval_harbor: Harbor CLI not found; install Harbor, then rerun this command.", file=sys.stderr)
        return 2

    job_name = f"claimlab-replay-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    command = [
        harbor,
        "run",
        "--path",
        str(args.tasks_dir.resolve()),
        "--agent",
        "oracle",
        "--job-name",
        job_name,
        "--jobs-dir",
        str((ROOT / "jobs").resolve()),
        "--n-concurrent",
        "1",
    ]
    _write_running_summary(cases)
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        summary = _write_summary(cases, ROOT / "jobs" / job_name, f"Harbor exited with status {exc.returncode}")
        print(json.dumps(summary, indent=2))
        return exc.returncode or 1
    summary = _write_summary(cases, ROOT / "jobs" / job_name)
    print("Harbor replay scores:")
    for task in summary["tasks"]:
        print(f"  {task['id']}: {task['reward']:.1f}")
    print(f"Score: {summary['passed']}/{summary['total']} ({summary['score']:.0%})")
    if summary["error"] is not None:
        print(summary["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
