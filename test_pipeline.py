import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline


class PipelineAdjudicationTests(unittest.TestCase):
    def test_rag_decision_uses_documents_without_expected_or_hidden_facts(self):
        import decisions_compare, policy_rag
        task = {'policy': [{'id': 'DEMO-DECL', 'text': 'Fictional declarations'}, {'id': 'OLD', 'text': 'PRESELECTED-SECRET'}],
                'policy_source': {'url': 'https://example.com/policy'}, 'transcript': [{'speaker': 'Claimant', 'text': 'A loss', 'phase': 'opening'}],
                'document_retrieval_required': True, 'expected': {'verdict': 'EXPECTED-SECRET'}, 'claimant_facts': 'PRIVATE-SECRET'}
        with self.assertRaisesRegex(ValueError, 'retrieval is required'):
            decisions_compare.request_for(task)
        task['policy_retrieval'] = {'source_evidence': policy_rag.evidence_for(task), 'passages': [{'id': 'p1', 'text': 'Retrieved terms'}], 'review': 'Review', 'limitation': 'Incomplete retrieval'}
        payload = json.dumps(decisions_compare.request_for(task))
        self.assertIn('Retrieved terms', payload)
        for secret in ('EXPECTED-SECRET', 'PRIVATE-SECRET', 'PRESELECTED-SECRET'):
            self.assertNotIn(secret, payload)

    def test_harbor_rewards_match_exact_case_names(self):
        import eval_harbor
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [{'id': name, 'slug': name} for name in ('auto-flood', 'auto-flood-declarations-missing')]
            for case, reward in zip(cases, (0.0, 1.0)):
                folder = root / (case['id'] + '__trial')
                folder.mkdir()
                (folder / 'result.json').write_text(json.dumps({'task_name': 'claimlab/replay-' + case['id'], 'verifier_result': {'rewards': {'reward': reward}}}))
            self.assertEqual(eval_harbor._read_rewards(root, cases), {'auto-flood': 0.0, 'auto-flood-declarations-missing': 1.0})

    def test_damage_regions_stay_inside_original_image(self):
        from annotate_damage import validate_annotations
        value = {'regions': [{'label': 'Dented bumper', 'box': [0.1, 0.2, 0.3, 0.4]}], 'uncertainty': 'Cause unknown.'}
        self.assertEqual(validate_annotations(value), value)
        value['regions'][0]['box'] = [0.9, 0.2, 0.3, 0.4]
        with self.assertRaisesRegex(ValueError, 'outside image'):
            validate_annotations(value)

    def test_followups_use_only_policy_transcript_and_image_then_stop_on_null(self):
        scenario = {
            "id": "auto-vandalism",
            "title": "Vandalism while parked",
            "category": "auto",
            "has_image": True,
            "policy": [{
                "id": "AUTO-VANDALISM-1",
                "text": "Coverage applies to malicious damage by another person; it does not apply when caused by the policyholder or an authorized driver.",
            }],
            "claimant_facts": {
                "incident": "The car was parked overnight and damage was found in the morning.",
                "damage": "The side window is broken.",
                "other_facts": "PRIVATE-CLAIMANT-ONLY: the owner denies causing the damage.",
            },
            "expected": {"rationale": "EXPECTED-SECRET"},
        }
        task = {
            "transcript": [{
                "id": "turn-1",
                "speaker": "Claimant",
                "text": "I found a broken side window on my parked car.",
                "audio": None,
                "phase": "opening",
            }],
            "followups_complete": False,
        }
        data = {"tasks": [task]}
        replies = [
            json.dumps({
                "question": "Do you know whether someone else caused the damage deliberately?",
                "why_needed": "The cited clause requires malicious damage by another person.",
                "clause_ids": ["AUTO-VANDALISM-1"],
            }),
            "I don't know who caused it or why.",
            json.dumps({
                "question": None,
                "why_needed": "The remaining actor and intent details are unknown, so the claim needs review.",
                "clause_ids": ["AUTO-VANDALISM-1"],
            }),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "claim.png"
            image_path.write_bytes(b"image-bytes")
            with patch.object(pipeline, "call_agent", side_effect=replies) as agent, patch.object(pipeline, "write_results"):
                pipeline.run_followups(scenario, task, data, "test-key", image_path)

        self.assertTrue(task["followups_complete"])
        self.assertEqual([turn["speaker"] for turn in task["transcript"]], ["Claimant", "Adjuster", "Claimant"])
        self.assertEqual(task["transcript"][1]["why_needed"], "The cited clause requires malicious damage by another person.")
        adjudicator_calls = [call for call in agent.call_args_list if call.args[0] == pipeline.ADJUDICATOR_MODEL]
        claimant_calls = [call for call in agent.call_args_list if call.args[0] == pipeline.CLAIMANT_MODEL]
        self.assertEqual(len(adjudicator_calls), 2)
        self.assertEqual(len(claimant_calls), 1)
        for call in adjudicator_calls:
            prompt = call.args[1] + json.dumps(call.args[2], ensure_ascii=False)
            self.assertNotIn("PRIVATE-CLAIMANT-ONLY", prompt)
            self.assertNotIn("EXPECTED-SECRET", prompt)
            self.assertNotIn("Vandalism while parked", prompt)
            self.assertEqual(call.args[2][0]["content"][-1]["type"], "input_image")
        second_prompt = json.dumps(adjudicator_calls[1].args[2], ensure_ascii=False)
        self.assertIn("I don't know who caused it or why.", second_prompt)

    def test_decisions_request_excludes_astra_and_expected(self):
        import decisions_compare
        task = {"policy": [{"id": "P1", "text": "Collision covered"}],
                "transcript": [{"speaker": "Claimant", "text": "A car hit mine", "phase": "opening"},
                               {"speaker": "Adjuster", "text": "ASTRA-SECRET", "phase": "decision"}],
                "expected": {"verdict": "EXPECTED-SECRET"}}
        payload = json.dumps(decisions_compare.request_for(task))
        self.assertIn("A car hit mine", payload)
        self.assertNotIn("ASTRA-SECRET", payload)
        self.assertNotIn("EXPECTED-SECRET", payload)
        self.assertIn("needs_review", payload)
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, 'car.png').write_bytes(b'car-image')
            task['image'] = '/car.png'
            task['damage_annotations'] = {'source_image': '/car.png', 'image_sha256': hashlib.sha256(b'car-image').hexdigest(),
                                          'regions': [{'label': 'Dented bumper', 'box': [0.1, 0.2, 0.3, 0.4]}], 'uncertainty': 'Cause unknown'}
            with patch.object(pipeline, 'PUBLIC', Path(temporary)):
                payload = json.dumps(decisions_compare.request_for(task))
                self.assertIn('Dented bumper', payload)
                self.assertIn('Cause unknown', payload)
                self.assertNotIn('ASTRA-SECRET', payload)
                task['damage_annotations']['image_sha256'] = 'outdated'
                with self.assertRaisesRegex(ValueError, 'current image'):
                    decisions_compare.request_for(task)

    def test_harbor_scores_luna_and_rejects_stale_evidence(self):
        import eval_harbor
        cases, saved = [], []
        for i in range(15):
            case = {"id": f"case-{i}", "title": "Claim", "policy": [{"id": "P", "text": "Policy"}],
                    "transcript": [{"speaker": "Claimant", "text": "Reported loss", "phase": "opening"}],
                    "image": None, "expected": {"verdict": "covered", "clause_ids": ["P"]},
                    "decision": {"verdict": "covered", "clause_ids": ["P"]}}
            cases.append(case)
            saved.append({"id": case["id"], "status": "complete", "verdict": "excluded",
                          "source_policy": case["policy"], "source_transcript": ["Reported loss"], "source_image": None})
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / "results.json"
            decisions = Path(temporary) / "decisions.json"
            results.write_text(json.dumps({"tasks": cases}))
            decisions.write_text(json.dumps({"model": "gpt-6-luna", "endpoint": "/v1/decisions", "tasks": saved}))
            loaded = eval_harbor.load_cases(results, decisions)
            self.assertEqual(len(loaded), 15)
            self.assertEqual(loaded[0]["decision"]["verdict"], "excluded")
            saved[0]["source_transcript"] = ["Different evidence"]
            decisions.write_text(json.dumps({"model": "gpt-6-luna", "endpoint": "/v1/decisions", "tasks": saved}))
            with self.assertRaisesRegex(ValueError, "stale evidence"):
                eval_harbor.load_cases(results, decisions)

    def test_decision_rejects_unknown_policy_clause(self):
        decision = {
            "verdict": "covered",
            "reason": "The claimant reports damage covered by the policy.",
            "clause_ids": ["NOT-IN-POLICY"],
            "confidence": 0.8,
            "evidence": [{
                "source": "turn-1",
                "observation": "The claimant reports damage.",
                "clause_ids": ["CLAUSE-1"],
            }],
            "missing_information": [],
            "uncertainty": "The cause is not independently verified.",
            "next_action": "Review the loss details.",
        }
        with self.assertRaisesRegex(RuntimeError, "unknown policy clause"):
            pipeline.validate_decision(
                decision,
                [{"id": "CLAUSE-1", "text": "Sudden damage is covered."}],
                [{"speaker": "Claimant", "text": "My property was damaged."}],
                False,
            )


class RealtimeSpeechTests(unittest.TestCase):
    class FakeSocket:
        def __init__(self, events):
            self.events = list(events)
            self.sent = []
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def recv(self):
            return json.dumps(self.events.pop(0))

        def send(self, event):
            self.sent.append(json.loads(event))

        def close(self):
            self.closed = True

    def test_realtime_request_uses_exact_script_and_collects_completed_audio(self):
        import base64
        import realtime_speech

        socket = self.FakeSocket([
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00").decode("ascii")},
            {"type": "response.output_audio_transcript.done", "transcript": "Hello there!"},
            {"type": "response.done", "response": {"status": "completed"}},
        ])
        with patch.object(realtime_speech, "_connect", return_value=socket) as connect:
            pcm, transcript = realtime_speech.synthesize("Hello there.", "marin", "Read naturally.", "test-key")

        self.assertEqual(pcm, b"\x01\x00")
        self.assertEqual(transcript, "Hello there!")
        connect.assert_called_once_with("test-key", realtime_speech.DEFAULT_TIMEOUT)
        self.assertEqual(socket.sent[0]["session"]["audio"]["output"]["voice"], "marin")
        self.assertEqual(socket.sent[0]["session"]["audio"]["output"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertEqual(socket.sent[1]["item"]["content"][0]["text"], "Hello there.")
        self.assertEqual(socket.sent[2]["type"], "response.create")
        self.assertTrue(socket.closed)

    def test_realtime_transcript_mismatch_fails_without_retry(self):
        import realtime_speech

        socket = self.FakeSocket([
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "response.output_audio.delta", "delta": "AQA="},
            {"type": "response.output_audio_transcript.done", "transcript": "Hello, someone."},
            {"type": "response.done", "response": {"status": "completed"}},
        ])
        with patch.object(realtime_speech, "_connect", return_value=socket) as connect:
            with self.assertRaisesRegex(RuntimeError, "differs from the supplied script"):
                realtime_speech.synthesize("Hello there.", "marin", "Read naturally.", "test-key")

        connect.assert_called_once()
        self.assertTrue(socket.closed)


if __name__ == "__main__":
    unittest.main()
