#!/usr/bin/env python3
"""Run the fictional insurance claim conversations and build their media."""

import base64
import fcntl
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import realtime_speech


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
MEDIA = PUBLIC / "media"
RESULTS = PUBLIC / "data" / "results.json"
HARBOR_SUMMARY = PUBLIC / "data" / "harbor-summary.json"
SCENARIOS = ROOT / "scenarios.json"
CLAIMANT_MODEL = "gpt-6-luna"
ADJUDICATOR_MODEL = "gpt-6-astra"
IMAGE_MODEL = "gpt-image-2"
VOICE_MODEL = realtime_speech.MODEL
CLAIMANT_VOICE = "marin"
ADJUSTER_VOICE = "cedar"
TIMEOUT = 240
API_TIMEOUT = 60
ADJUDICATION_VERSION = "v4"
MAX_FOLLOWUPS = 2
RUN_LOCK_PATH = Path(tempfile.gettempdir()) / (
    "codex-insurance-demo-" + hashlib.sha256(str(ROOT).encode("utf-8")).hexdigest()[:12] + ".lock"
)


class RunAlreadyActive(RuntimeError):
    pass


def acquire_run_lock():
    lock_file = RUN_LOCK_PATH.open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise RunAlreadyActive("A benchmark run is already active") from None
    return lock_file


def release_run_lock(lock_file):
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    lock_file.close()


def load_api_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    env_file = ROOT / ".env"
    if not key and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            name, separator, value = line.partition("=")
            if separator and name.strip() == "OPENAI_API_KEY":
                key = value.strip().strip("\"'")
                break
    if not key:
        raise RuntimeError("OPENAI_API_KEY was not found in the environment or local .env")
    return key


def safe_error(message, key):
    message = str(message)
    if key:
        message = message.replace(key, "[redacted]")
    return message[:800]


def api_request(path, key, payload=None, binary=False, timeout=API_TIMEOUT):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": "Bearer " + key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request("https://api.openai.com" + path, data=body, headers=headers)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = response.read()
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("OpenAI " + path + " returned HTTP " + str(exc.code) + ": " + safe_error(detail, key)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 0:
                continue
            raise RuntimeError("OpenAI " + path + " request failed: " + safe_error(exc, key)) from None
    if binary:
        return result
    try:
        return json.loads(result)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenAI " + path + " returned invalid JSON: " + safe_error(exc, key)) from None


def response_text(response):
    direct = response.get("output_text")
    if direct:
        return direct.strip()
    chunks = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(content["text"])
    text = "\n".join(chunks).strip()
    if not text:
        raise RuntimeError("Responses API returned no text output")
    return text


def call_agent(model, instructions, messages, key, max_tokens=240):
    return response_text(api_request("/v1/responses", key, {
        "model": model,
        "instructions": instructions,
        "input": messages,
        "max_output_tokens": max_tokens,
    }))


def text_message(role, text):
    return {"role": role, "content": text}


def claimant_facts_context(scenario):
    return json.dumps({
        "claimant_facts": scenario["claimant_facts"],
    }, ensure_ascii=False)


def adjudicator_message(scenario, transcript, image_path, request):
    policy_context = {
        "case_category": scenario["category"],
        "policy_clauses": scenario["policy"],
    }
    transcript_text = "\n".join(
        f"turn-{index}: {turn['speaker']}: {turn['text']}"
        for index, turn in enumerate(transcript, start=1)
    ) or "(No conversation turns yet.)"
    text = (
        request + "\nPolicy: " + json.dumps(policy_context, ensure_ascii=False)
        + "\nActual conversation transcript:\n" + transcript_text
    )
    content = [{"type": "input_text", "text": text}]
    if scenario["has_image"] and image_path is not None:
        image_data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        content.append({"type": "input_image", "image_url": "data:image/png;base64," + image_data, "detail": "high"})
    return {"role": "user", "content": content}


def parse_json_object(raw_text, label, key):
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(label + " returned invalid JSON: " + safe_error(exc, key)) from None
    if not isinstance(value, dict):
        raise RuntimeError(label + " must return a JSON object")
    return value


def validate_clause_ids(clause_ids, valid_clause_ids, label):
    if not isinstance(clause_ids, list) or not all(isinstance(clause_id, str) for clause_id in clause_ids):
        raise RuntimeError(label + " must be an array of clause ID strings")
    unknown = set(clause_ids) - valid_clause_ids
    if unknown:
        raise RuntimeError(label + " cited unknown policy clause IDs: " + ", ".join(sorted(unknown)))
    return clause_ids


def validate_followup(value, policy):
    question = value.get("question")
    why_needed = value.get("why_needed")
    if question is not None and (not isinstance(question, str) or not question.strip()):
        raise RuntimeError("Follow-up JSON question must be a non-empty string or null")
    if not isinstance(why_needed, str) or not why_needed.strip():
        raise RuntimeError("Follow-up JSON why_needed must be a non-empty string")
    clause_ids = validate_clause_ids(
        value.get("clause_ids"), {clause["id"] for clause in policy}, "Follow-up JSON clause_ids"
    )
    return {"question": question, "why_needed": why_needed, "clause_ids": clause_ids}


def validate_decision(value, policy, transcript, has_image):
    verdict = value.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"covered", "excluded", "needs_review"}:
        raise RuntimeError("Adjudicator JSON verdict must be covered, excluded, or needs_review")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise RuntimeError("Adjudicator JSON reason must be a non-empty string")
    clause_ids = validate_clause_ids(
        value.get("clause_ids"), {clause["id"] for clause in policy}, "Adjudicator JSON clause_ids"
    )
    if not clause_ids:
        raise RuntimeError("Adjudicator JSON clause_ids must cite at least one relevant policy clause")
    confidence = value.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise RuntimeError("Adjudicator JSON confidence must be finite and between 0 and 1")

    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise RuntimeError("Adjudicator JSON evidence must be a non-empty array")
    normalized_evidence = []
    for item in evidence:
        if not isinstance(item, dict):
            raise RuntimeError("Adjudicator JSON evidence entries must be objects")
        source = item.get("source")
        if source == "image":
            if not has_image:
                raise RuntimeError("Adjudicator JSON cited an image that was not supplied")
        elif isinstance(source, str) and re.fullmatch(r"turn-[1-9][0-9]*", source):
            turn_number = int(source[5:])
            if turn_number > len(transcript) or transcript[turn_number - 1].get("speaker") != "Claimant":
                raise RuntimeError("Adjudicator JSON cited a non-claimant or nonexistent transcript turn")
        else:
            raise RuntimeError("Adjudicator JSON evidence source must be image or a valid turn-N")
        observation = item.get("observation")
        if not isinstance(observation, str) or not observation.strip():
            raise RuntimeError("Adjudicator JSON evidence observation must be a non-empty string")
        evidence_clause_ids = validate_clause_ids(
            item.get("clause_ids"), {clause["id"] for clause in policy}, "Evidence clause_ids"
        )
        normalized_evidence.append({
            "source": source,
            "observation": observation,
            "clause_ids": evidence_clause_ids,
        })

    missing_information = value.get("missing_information")
    if not isinstance(missing_information, list) or not all(isinstance(item, str) and item.strip() for item in missing_information):
        raise RuntimeError("Adjudicator JSON missing_information must be an array of strings")
    for field in ("uncertainty", "next_action"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise RuntimeError("Adjudicator JSON " + field + " must be a non-empty string")
    return {
        "verdict": value["verdict"],
        "reason": value["reason"],
        "clause_ids": clause_ids,
        "confidence": float(confidence),
        "evidence": normalized_evidence,
        "missing_information": missing_information,
        "uncertainty": value["uncertainty"],
        "next_action": value["next_action"],
    }


def generate_image(scenario, key, image_path):
    image_path.parent.mkdir(parents=True, exist_ok=True)
    payload = api_request("/v1/images/generations", key, {
        "model": IMAGE_MODEL,
        "prompt": scenario["image_prompt"],
        "size": "1024x1024",
        "n": 1,
    }, timeout=TIMEOUT)
    image = payload.get("data", [{}])[0]
    if image.get("b64_json"):
        image_bytes = base64.b64decode(image["b64_json"])
    elif image.get("url"):
        request = urllib.request.Request(image["url"])
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            image_bytes = response.read()
    else:
        raise RuntimeError("Image generation returned neither b64_json nor a URL")
    if not image_bytes:
        raise RuntimeError("Image generation returned an empty image")
    image_path.write_bytes(image_bytes)


def wav_duration(path):
    path = Path(path)
    with wave.open(str(path), "rb") as audio:
        frame_size = audio.getnchannels() * audio.getsampwidth()
        sample_rate = audio.getframerate()
    content = bytearray(path.read_bytes())
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        offset = 12
        while offset + 8 <= len(content):
            chunk_id = content[offset:offset + 4]
            chunk_size = int.from_bytes(content[offset + 4:offset + 8], "little")
            payload_start = offset + 8
            available = len(content) - payload_start
            if chunk_id == b"data":
                if chunk_size > available:
                    actual_size = available - (available % frame_size)
                    content[offset + 4:offset + 8] = actual_size.to_bytes(4, "little")
                    content[4:8] = (len(content) - 8).to_bytes(4, "little")
                    path.write_bytes(content)
                return actual_size / (frame_size * sample_rate) if chunk_size > available else chunk_size / (frame_size * sample_rate)
            offset = payload_start + chunk_size + (chunk_size & 1)
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def synthesize(text, voice, target, key):
    if voice == CLAIMANT_VOICE:
        style = (
            "Read as a person reporting a loss on a phone call: natural, conversational, and understated. "
            "Let any emotion in the words come through subtly while staying composed. "
        )
    else:
        style = (
            "Read as a calm, clear, attentive claims adjuster on a phone call, with restrained warmth and brief natural pauses. "
            "Keep the script's stated outcome as written. "
        )
    style += "Speak at a natural conversational pace. Say exactly the supplied words; add no words, facts, or sound effects."
    digest = hashlib.sha256((VOICE_MODEL + "\0" + voice + "\0" + style + "\0" + text).encode("utf-8")).hexdigest()[:12]
    audio_path = target.with_name(target.stem + "_" + digest + ".wav")
    metadata_path = audio_path.with_suffix(".json")
    cached = None
    try:
        cached = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if not (
        audio_path.is_file() and isinstance(cached, dict)
        and cached.get("model") == VOICE_MODEL and cached.get("voice") == voice
        and cached.get("script") == text
        and isinstance(cached.get("transcript"), str)
        and realtime_speech._normalized_words(cached["transcript"]) == realtime_speech._normalized_words(text)
    ):
        pcm, spoken_transcript = realtime_speech.synthesize(text, voice, style, key)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(realtime_speech.SAMPLE_RATE)
            audio.writeframes(pcm)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_audio = audio_path.with_suffix(".wav.tmp")
        temporary_audio.write_bytes(wav_buffer.getvalue())
        os.replace(temporary_audio, audio_path)
        temporary_metadata = metadata_path.with_suffix(".json.tmp")
        temporary_metadata.write_text(json.dumps({
            "model": VOICE_MODEL,
            "voice": voice,
            "script": text,
            "transcript": spoken_transcript,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary_metadata, metadata_path)
    duration = wav_duration(audio_path)
    if duration <= 0:
        raise RuntimeError("Speech API returned an empty WAV")
    return audio_path, duration


def srt_time(seconds):
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"


def write_subtitles(transcript, durations, target, vtt_target):
    offset = 0.0
    srt_cues, vtt_cues = [], []
    for number, (turn, duration) in enumerate(zip(transcript, durations), start=1):
        end = offset + duration + (1.5 if number == len(transcript) else 0)
        caption = re.sub(r"\s+", " ", turn["text"]).replace("-->", "—>")
        srt_cues.append(f"{number}\n{srt_time(offset)} --> {srt_time(end)}\n{turn['speaker']}: {caption}\n")
        vtt_cues.append(f"{srt_time(offset).replace(',', '.')} --> {srt_time(end).replace(',', '.')}\n{turn['speaker']}: {caption}\n")
        offset = end
        offset -= 1.5 if number == len(transcript) else 0
    target.write_text("\n".join(srt_cues), encoding="utf-8")
    vtt_target.write_text("WEBVTT\n\n" + "\n".join(vtt_cues), encoding="utf-8")
    return sum(durations)


def run_ffmpeg(command, key):
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is required to assemble the demo videos") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timed out while assembling media") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "ffmpeg failed").strip()
        raise RuntimeError("ffmpeg failed: " + safe_error(detail, key)) from None
    return result


def assemble_video(scenario, media_dir, transcript, audio_paths, audio_durations, key):
    subtitle_path = media_dir / "captions.srt"
    vtt_path = media_dir / "captions.vtt"
    video_path = media_dir / "conversation.mp4"
    duration = write_subtitles(transcript, audio_durations, subtitle_path, vtt_path)

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if scenario["has_image"]:
        image_hash = hashlib.sha256(scenario["image_prompt"].encode("utf-8")).hexdigest()[:10]
        image_path = media_dir.parent / ("claim_" + image_hash + ".png")
        args += ["-loop", "1", "-framerate", "30", "-i", str(image_path)]
    else:
        args += ["-f", "lavfi", "-i", f"color=c=0x10283f:s=1280x720:r=30:d={duration + 2:.3f}"]
    for audio_path in audio_paths:
        args += ["-i", str(audio_path)]
    args += ["-i", str(subtitle_path)]
    audio_inputs = "".join(f"[{index}:a]" for index in range(1, len(audio_paths) + 1))
    args += [
        "-filter_complex", f"{audio_inputs}concat=n={len(audio_paths)}:v=0:a=1[outa]",
        "-map", "0:v:0", "-map", "[outa]",
        "-map", f"{len(audio_paths) + 1}:s:0",
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x10283f",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "128k",
        "-c:s", "mov_text", "-shortest", "-movflags", "+faststart",
        "-metadata", "title=" + scenario["title"],
        "-metadata", "comment=Fictional insurance training demonstration",
        str(video_path),
    ]
    run_ffmpeg(args, key)
    return video_path, vtt_path


def public_url(path):
    return "/" + path.resolve().relative_to(PUBLIC.resolve()).as_posix()


def write_results(data):
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    temporary = RESULTS.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, RESULTS)


def evaluation(decision, expected, policy):
    verdict_correct = decision["verdict"] == expected["verdict"]
    required = set(expected["clause_ids"])
    valid = {clause["id"] for clause in policy}
    clause_correct = required.issubset(set(decision["clause_ids"])) and set(decision["clause_ids"]).issubset(valid)
    score = (int(verdict_correct) + int(clause_correct)) / 2
    return verdict_correct, clause_correct, score


def update_benchmark(data):
    completed = [task for task in data["tasks"] if task["status"] == "complete"]
    passed = sum(bool(task["metrics"]["verdict_correct"] and task["metrics"]["clause_correct"]) for task in completed)
    correct = sum(bool(task["metrics"]["verdict_correct"]) for task in completed)
    total = len(data["tasks"])
    data["benchmark"] = {
        "completed": len(completed),
        "total": total,
        "passed": passed,
        "accuracy": round(correct / len(completed), 3) if completed else 0,
    }


def actual_voice_model(data, fallback=None):
    if data.get("status") != "complete" or any(task.get("status") != "complete" for task in data.get("tasks", [])):
        return fallback
    voices = {
        turn.get("voice_model", fallback)
        for task in data.get("tasks", [])
        for turn in task.get("transcript", [])
    }
    voices.discard(None)
    if len(voices) == 1:
        return next(iter(voices))
    return "mixed" if voices else fallback


def mark_harbor_pending():
    try:
        summary = json.loads(HARBOR_SUMMARY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(summary, dict):
        return
    summary.update({
        "status": "pending",
        "passed": None,
        "total": None,
        "score": None,
        "tasks": [],
        "error": None,
        "note": "Pending replay of the latest adjudication results.",
    })
    temporary = HARBOR_SUMMARY.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, HARBOR_SUMMARY)


def initial_task(scenario):
    digest = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "category": scenario["category"],
        "has_image": scenario["has_image"],
        "document_retrieval_required": not scenario["has_image"],
        "image_label": scenario["image_label"],
        "adjudication_version": ADJUDICATION_VERSION,
        "status": "pending",
        "policy": scenario["policy"],
        "policy_source": scenario.get("policy_source"),
        "challenge": scenario.get("challenge"),
        "claimant_facts": scenario["claimant_facts"],
        "expected": scenario["expected"],
        "decision": None,
        "followups_complete": False,
        "transcript": [],
        "image": None,
        "video": None,
        "metrics": {"verdict_correct": None, "clause_correct": None, "score": None, "latency_seconds": 0, "adjudication_seconds": 0},
        "error": None,
        "scenario_hash": digest,
    }


def cached_task_is_valid(task, scenario):
    if task.get("status") != "complete" or task.get("adjudication_version") != ADJUDICATION_VERSION or not task.get("scenario_hash"):
        return False
    if task["scenario_hash"] != initial_task(scenario)["scenario_hash"]:
        return False
    paths = [task.get("video"), task.get("captions")]
    if scenario["has_image"]:
        paths.append(task.get("image"))
    paths.extend(turn.get("audio") for turn in task.get("transcript", []))
    for url in paths:
        if not url or not url.startswith("/media/") or not (PUBLIC / url.lstrip("/")).is_file():
            return False
    transcript = task.get("transcript", [])
    if len(transcript) not in {2, 4, 6} or not isinstance(task.get("decision"), dict):
        return False
    media_urls = [task.get("video"), task.get("captions"), *(turn.get("audio") for turn in transcript)]
    case_prefix = f"/media/{scenario['id']}/"
    versions = {url[len(case_prefix):].split("/", 1)[0] for url in media_urls if isinstance(url, str) and url.startswith(case_prefix)}
    return len(versions) == 1 and versions.issubset({ADJUDICATION_VERSION, "v4-realtime"}) and all(
        isinstance(url, str) and url.startswith(case_prefix + next(iter(versions)) + "/") for url in media_urls
    )


def resumable_task_is_valid(task, scenario):
    if task.get("adjudication_version") != ADJUDICATION_VERSION:
        return False
    if task.get("scenario_hash") != initial_task(scenario)["scenario_hash"]:
        return False
    return isinstance(task.get("transcript"), list) and isinstance(task.get("metrics"), dict)


def get_previous_results():
    try:
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def scenario_image_path(scenario):
    if not scenario["has_image"]:
        return None
    image_hash = hashlib.sha256(scenario["image_prompt"].encode("utf-8")).hexdigest()[:10]
    return MEDIA / scenario["id"] / ("claim_" + image_hash + ".png")


def transcript_text(transcript):
    return "\n".join(f"{turn['speaker']}: {turn['text']}" for turn in transcript)


def append_turn(task, speaker, model, text, phase, round_number=None, **metadata):
    turn = {
        "id": f"turn-{len(task['transcript']) + 1}",
        "speaker": speaker,
        "model": model,
        "text": text,
        "audio": None,
        "phase": phase,
    }
    if round_number is not None:
        turn["round"] = round_number
    turn.update(metadata)
    task["transcript"].append(turn)


def run_followups(scenario, task, data, key, image_path):
    task.setdefault("followups_complete", False)
    if task["followups_complete"]:
        return
    followup_instructions = (
        "You are an insurance claims adjuster. Use only the supplied policy clauses, actual conversation transcript, "
        "and supplied image. The image may support visible physical conditions only; it cannot establish dates, actor identity, or intent. "
        "Ask one concise question only when a missing fact could materially change how an explicit policy clause applies. "
        "Phrase it for a real phone call: use plain language and contractions, and briefly acknowledge the previous answer where helpful. "
        "Do not read clause IDs aloud or imply coverage is confirmed. "
        "Do not ask dates or other routine details unless a supplied clause makes them relevant. Do not repeat a question already answered, "
        "including one the claimant answered that they do not know. If no policy-relevant question is needed, set question to null. "
        "Return exactly one JSON object with question (string or null), why_needed (string), and clause_ids (array of supplied policy IDs)."
    )
    claimant_instructions = (
        "You are the claimant. Answer the adjuster's latest question naturally in first person using only the supplied private facts. "
        "If you do not know, say naturally, 'I don't know.' Do not invent details, discuss coverage, or mention a fictional or training setup. "
        "Keep the answer to one or two short sentences."
        " Use everyday phrasing and contractions; answer directly, without repeating the entire incident or adding scripted filler."
    )

    for round_number in range(1, MAX_FOLLOWUPS + 1):
        question_turn = next((
            turn for turn in task["transcript"]
            if turn.get("phase") == "followup" and turn.get("round") == round_number
        ), None)
        if question_turn is None:
            raw = call_agent(ADJUDICATOR_MODEL, followup_instructions, [
                adjudicator_message(
                    scenario,
                    task["transcript"],
                    image_path,
                    "Choose the next policy-relevant clarification, or stop if the current information is enough. Do not use any hidden claimant facts.",
                )
            ], key, max_tokens=320)
            followup = validate_followup(parse_json_object(raw, "Adjudicator follow-up", key), scenario["policy"])
            if followup["question"] is None:
                task["followups_complete"] = True
                task["followup_stop"] = {
                    "why_needed": followup["why_needed"],
                    "clause_ids": followup["clause_ids"],
                }
                write_results(data)
                return
            question_turn = {
                "id": f"turn-{len(task['transcript']) + 1}",
                "speaker": "Adjuster",
                "model": ADJUDICATOR_MODEL,
                "text": followup["question"],
                "audio": None,
                "phase": "followup",
                "round": round_number,
                "why_needed": followup["why_needed"],
                "clause_ids": followup["clause_ids"],
            }
            task["transcript"].append(question_turn)
            write_results(data)

        response_turn = next((
            turn for turn in task["transcript"]
            if turn.get("phase") == "reply" and turn.get("round") == round_number
        ), None)
        if response_turn is None:
            response = call_agent(CLAIMANT_MODEL, claimant_instructions, [
                text_message("user", "Private claimant facts: " + claimant_facts_context(scenario)),
                text_message("user", "Conversation so far:\n" + transcript_text(task["transcript"])),
                text_message("user", "Answer the latest adjuster question now."),
            ], key, max_tokens=120)
            append_turn(task, "Claimant", CLAIMANT_MODEL, response, "reply", round_number)
            write_results(data)

    task["followups_complete"] = True
    write_results(data)


def run_task(scenario, task, data, key):
    started = time.monotonic()
    prior_latency = float(task.get("metrics", {}).get("latency_seconds", 0) or 0)
    media_dir = MEDIA / scenario["id"] / ADJUDICATION_VERSION
    media_dir.mkdir(parents=True, exist_ok=True)
    task["status"] = "running"
    task["error"] = None
    task["adjudication_version"] = ADJUDICATION_VERSION
    task.setdefault("transcript", [])
    task.setdefault("metrics", {})
    task["metrics"].setdefault("adjudication_seconds", 0)
    write_results(data)
    print(f"{scenario['id']}: starting", flush=True)

    image_path = scenario_image_path(scenario)
    if image_path is not None:
        image_path.parent.mkdir(parents=True, exist_ok=True)
        if not image_path.is_file():
            generate_image(scenario, key, image_path)
        task["image"] = public_url(image_path)
        print(f"{scenario['id']}: image ready", flush=True)

    if not task["transcript"]:
        claimant_instructions = (
            "You are the claimant. Speak naturally in first person in one or two short sentences, describing only what happened and the physical damage or loss. "
            "Use the incident and damage facts; do not give unrelated background, claim-process details, policy opinions, or fictional/training meta. "
            "These facts are private to you and are not shared with the adjuster."
            " Start like a person calling to report a loss, with a brief greeting and everyday phrasing rather than a formal report."
            " Use contractions and natural sentence lengths. Do not invent feelings, injuries, dates, names, or other facts."
        )
        claim = call_agent(CLAIMANT_MODEL, claimant_instructions, [
            text_message("user", "Private claimant facts: " + claimant_facts_context(scenario)),
            text_message("user", "Give your brief first-person account now."),
        ], key, max_tokens=120)
        append_turn(task, "Claimant", CLAIMANT_MODEL, claim, "opening")
        write_results(data)

    if not task.get("decision"):
        run_followups(scenario, task, data, key, image_path)
        final_instructions = (
            "You are an insurance claims adjudicator. Apply only the supplied policy clauses and the actual claimant transcript; use the supplied image only for visible physical conditions. "
            "An image cannot establish incident dates, actor identity, or intent. Use only prerequisites stated in the supplied policy; do not invent any. "
            "Do not treat missing dates as a gap unless an explicit clause makes them material. Do not treat adjuster questions as evidence. "
            "Return exactly one JSON object with verdict, reason, clause_ids, confidence, evidence, missing_information, uncertainty, and next_action. "
            "verdict must be covered, excluded, or needs_review. reason must be one or two concise sentences connecting claimant evidence to the relevant policy language and stating any material gap. "
            "clause_ids must cite at least one relevant supplied policy ID. confidence is your self-assessed confidence in the selected verdict label, not the probability that the claim is covered; it may be high for needs_review and is not calibrated. "
            "evidence must be a non-empty array of objects with source (a claimant turn-N or image), observation, and clause_ids; cite only claimant turns or the supplied image. "
            "missing_information must list only facts that matter under a supplied clause. uncertainty and next_action must be concise strings suitable for a human claim file."
            " Do not mention fictional or training meta in reason or next_action."
        )
        decision_request = (
            "Make the final policy decision and provide grounded evidence. Cite transcript turns as turn-N, using claimant turns only; cite image only for visible physical conditions. "
            "Return valid JSON without Markdown."
        )
        final_started = time.monotonic()
        prior_adjudication_seconds = float(task["metrics"].get("adjudication_seconds", 0) or 0)
        try:
            raw_decision = call_agent(ADJUDICATOR_MODEL, final_instructions, [
                adjudicator_message(scenario, task["transcript"], image_path, decision_request)
            ], key, max_tokens=1800)
        finally:
            task["metrics"]["adjudication_seconds"] = round(
                prior_adjudication_seconds + time.monotonic() - final_started, 2
            )
            write_results(data)
        decision = validate_decision(
            parse_json_object(raw_decision, "Adjudicator", key),
            scenario["policy"],
            task["transcript"],
            image_path is not None,
        )
        task["decision"] = decision
        spoken_label = "requires further review" if decision["verdict"] == "needs_review" else decision["verdict"]
        spoken_decision = f"Decision: {spoken_label}. {decision['reason']} Next action: {decision['next_action']}"
        append_turn(task, "Adjuster", ADJUDICATOR_MODEL, spoken_decision, "decision")
        write_results(data)

    decision = task["decision"]
    audio_paths, audio_durations = [], []
    for index, turn in enumerate(task["transcript"], start=1):
        voice = CLAIMANT_VOICE if turn["speaker"] == "Claimant" else ADJUSTER_VOICE
        existing_audio = turn.get("audio")
        existing_path = PUBLIC / existing_audio.lstrip("/") if isinstance(existing_audio, str) and existing_audio.startswith("/media/") else None
        if (
            existing_path is not None and existing_path.is_file()
            and turn.get("voice_model") == VOICE_MODEL and turn.get("voice") == voice
        ):
            try:
                duration = wav_duration(existing_path)
            except (OSError, wave.Error):
                duration = 0
            audio_path = existing_path
        else:
            duration = 0
        if duration <= 0:
            audio_path, duration = synthesize(turn["text"], voice, media_dir / f"turn_{index}.wav", key)
            turn["audio"] = public_url(audio_path)
        turn["voice_model"] = VOICE_MODEL
        turn["voice"] = voice
        audio_paths.append(audio_path)
        audio_durations.append(duration)
        write_results(data)
        print(f"{scenario['id']}: speech {index}/{len(task['transcript'])} ready", flush=True)

    video_path, captions_path = assemble_video(
        scenario, media_dir, task["transcript"], audio_paths, audio_durations, key
    )
    task["video"] = public_url(video_path)
    task["captions"] = public_url(captions_path)
    verdict_correct, clause_correct, score = evaluation(decision, scenario["expected"], scenario["policy"])
    task["metrics"] = {
        "verdict_correct": verdict_correct,
        "clause_correct": clause_correct,
        "score": score,
        "latency_seconds": round(prior_latency + time.monotonic() - started, 2),
        "adjudication_seconds": task["metrics"].get("adjudication_seconds", 0),
    }
    task["status"] = "complete"
    task["error"] = None
    update_benchmark(data)
    write_results(data)
    print(f"{scenario['id']}: complete ({decision['verdict']}; expected {scenario['expected']['verdict']})", flush=True)


def _run_pipeline():
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    previous = get_previous_results()
    old_tasks = {task.get("id"): task for task in previous.get("tasks", []) if isinstance(task, dict)}
    data = {
        "status": "running",
        "models": {
            "claimant": CLAIMANT_MODEL,
            "adjudicator": ADJUDICATOR_MODEL,
            "image": IMAGE_MODEL,
            "voice": (previous.get("models") or {}).get("voice"),
        },
        "tasks": [],
        "benchmark": {"completed": 0, "total": len(scenarios), "passed": 0, "accuracy": 0},
    }
    stale_tasks = any(
        not cached_task_is_valid(old_tasks.get(scenario["id"], {}), scenario)
        for scenario in scenarios
    )
    if previous.get("tasks") and stale_tasks:
        mark_harbor_pending()
    for scenario in scenarios:
        task = old_tasks.get(scenario["id"], {})
        if cached_task_is_valid(task, scenario):
            data["tasks"].append(task)
        elif resumable_task_is_valid(task, scenario):
            if task.get("status") == "complete":
                task["status"] = "pending"
                task["error"] = None
            data["tasks"].append(task)
        else:
            data["tasks"].append(initial_task(scenario))
    update_benchmark(data)
    write_results(data)

    try:
        key = load_api_key()
    except Exception as exc:
        message = safe_error(exc, "")
        for task in data["tasks"]:
            if task["status"] != "complete":
                task["status"] = "error"
                task["error"] = message
        data["status"] = "error"
        update_benchmark(data)
        write_results(data)
        print("Pipeline error: " + message, file=sys.stderr, flush=True)
        return data
    for scenario, task in zip(scenarios, data["tasks"]):
        if task["status"] == "complete":
            print(f"{scenario['id']}: cached", flush=True)
            continue
        task_started = time.monotonic()
        try:
            run_task(scenario, task, data, key)
        except Exception as exc:
            task["status"] = "error"
            task["error"] = safe_error(exc, key)
            prior_latency = float(task.get("metrics", {}).get("latency_seconds", 0) or 0)
            task["metrics"]["latency_seconds"] = round(prior_latency + time.monotonic() - task_started, 2)
            update_benchmark(data)
            write_results(data)
            print(f"{scenario['id']}: ERROR {task['error']}", file=sys.stderr, flush=True)
    data["status"] = "complete" if all(task["status"] == "complete" for task in data["tasks"]) else "partial"
    data["models"]["voice"] = actual_voice_model(data, (previous.get("models") or {}).get("voice"))
    update_benchmark(data)
    write_results(data)
    print(f"Benchmark: {data['benchmark']['completed']}/{data['benchmark']['total']} complete, {data['benchmark']['passed']} passed", flush=True)
    return data


def run_pipeline(lock_file=None):
    owns_lock = lock_file is None
    if owns_lock:
        lock_file = acquire_run_lock()
    try:
        return _run_pipeline()
    finally:
        if owns_lock:
            release_run_lock(lock_file)


if __name__ == "__main__":
    try:
        result = run_pipeline()
        sys.exit(0 if result["status"] == "complete" else 1)
    except Exception as exc:
        print("Pipeline error: " + safe_error(exc, os.environ.get("OPENAI_API_KEY", "")), file=sys.stderr)
        sys.exit(1)
