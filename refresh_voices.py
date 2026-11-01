#!/usr/bin/env python3
"""Regenerate all saved conversation voices without rerunning claim decisions."""

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pipeline


MODEL = pipeline.VOICE_MODEL
STAGING = pipeline.ROOT / "backup" / "staging" / "realtime-voice"


def _validate(data, scenarios):
    tasks = data.get("tasks")
    if len(scenarios) != 15 or not isinstance(tasks, list) or len(tasks) != 15:
        raise RuntimeError("Voice refresh requires exactly 15 saved cases")
    by_id = {task.get("id"): task for task in tasks if isinstance(task, dict)}
    if len(by_id) != len(tasks) or set(by_id) != {case["id"] for case in scenarios}:
        raise RuntimeError("Voice refresh found missing or duplicate saved cases")
    for scenario in scenarios:
        if not pipeline.cached_task_is_valid(by_id[scenario["id"]], scenario):
            raise RuntimeError("Voice refresh requires 15 complete cases with valid saved media")
    return by_id


def _already_refreshed(data, tasks):
    if (data.get("models") or {}).get("voice") != MODEL:
        return False
    for task in tasks.values():
        if task.get("voice_model") != MODEL or any(
            turn.get("voice_model") != MODEL or not str(turn.get("audio", "")).startswith(
                f"/media/{task['id']}/v4-realtime/"
            ) or turn.get("voice") != (pipeline.CLAIMANT_VOICE if turn["speaker"] == "Claimant" else pipeline.ADJUSTER_VOICE)
            for turn in task["transcript"]
        ):
            return False
    return True


def refresh_voices():
    lock = pipeline.acquire_run_lock()
    try:
        source = pipeline.get_previous_results()
        scenarios = json.loads(pipeline.SCENARIOS.read_text(encoding="utf-8"))
        source_tasks = _validate(source, scenarios)
        if _already_refreshed(source, source_tasks):
            print("All 15 cases already use " + MODEL, flush=True)
            return source

        key = pipeline.load_api_key()
        STAGING.mkdir(parents=True, exist_ok=True)
        snapshot = STAGING / "source-results.json"
        snapshot.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged = copy.deepcopy(source)
        staged_tasks = {task["id"]: task for task in staged["tasks"]}
        final_media = {}

        for scenario in scenarios:
            task = staged_tasks[scenario["id"]]
            case_stage = STAGING / scenario["id"]
            media_stage = case_stage / "v4-realtime"
            media_stage.mkdir(parents=True, exist_ok=True)
            if scenario["has_image"]:
                image_url = task.get("image")
                image_source = pipeline.PUBLIC / image_url.lstrip("/")
                image_stage = case_stage / image_source.name
                shutil.copy2(image_source, image_stage)

            audio_paths, durations = [], []
            for index, turn in enumerate(task["transcript"], start=1):
                voice = pipeline.CLAIMANT_VOICE if turn["speaker"] == "Claimant" else pipeline.ADJUSTER_VOICE
                audio_path, duration = pipeline.synthesize(turn["text"], voice, media_stage / f"turn_{index}.wav", key)
                turn["audio"] = pipeline.public_url(pipeline.MEDIA / scenario["id"] / "v4-realtime" / audio_path.name)
                turn["voice_model"] = MODEL
                turn["voice"] = voice
                audio_paths.append(audio_path)
                durations.append(duration)

            pipeline.assemble_video(scenario, media_stage, task["transcript"], audio_paths, durations, key)
            final_dir = pipeline.MEDIA / scenario["id"] / "v4-realtime"
            task["video"] = pipeline.public_url(final_dir / "conversation.mp4")
            task["captions"] = pipeline.public_url(final_dir / "captions.vtt")
            task["voice_model"] = MODEL
            final_media[scenario["id"]] = (media_stage, final_dir)
            print(f"{scenario['id']}: staged {len(audio_paths)} clips", flush=True)

        staged["models"]["voice"] = MODEL
        staged_path = STAGING / "staged-results.json"
        temporary = staged_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(staged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, staged_path)

        for media_stage, final_dir in final_media.values():
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(media_stage, final_dir, dirs_exist_ok=True)
        pipeline.write_results(staged)
        print("Voice refresh published for all 15 cases", flush=True)
        return staged
    finally:
        pipeline.release_run_lock(lock)


if __name__ == "__main__":
    try:
        refresh_voices()
    except Exception as exc:
        print("Voice refresh error: " + pipeline.safe_error(exc, os.environ.get("OPENAI_API_KEY", "")), file=sys.stderr)
        sys.exit(1)
