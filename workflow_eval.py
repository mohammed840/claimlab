"""Combine Harbor verdict rewards with a transparent, frozen interview audit."""
import json
from pathlib import Path
import eval_harbor

ROOT = Path(__file__).resolve().parent


def evaluate(results, decisions, harbor, rubric):
    tasks = results['tasks']
    predictions = {t['id']: t for t in decisions['tasks']}
    audits = {t['id']: t for t in rubric['tasks']}
    rewards = {t['id']: t for t in harbor['tasks']}
    ids = {t['id'] for t in tasks}
    if harbor.get('status') != 'completed' or harbor.get('error'):
        raise ValueError('Complete Harbor replay first')
    if any(len(items) != len(tasks) or set(items) != ids for items in [predictions, audits, rewards]):
        raise ValueError('Expected one prediction, audit and Harbor reward per case')
    output = []
    for task in tasks:
        audit, prediction, reward = audits[task['id']], predictions[task['id']], rewards[task['id']]
        interview = [{k: t[k] for k in ('id', 'speaker', 'text', 'phase')} for t in task['transcript'] if t.get('phase') != 'decision']
        if audit['source_interview'] != interview or audit['source_policy'] != task['policy']:
            raise ValueError('Stale interview audit: ' + task['id'])
        if audit['policy_clause'] not in {c['id'] for c in task['policy']} or not set(audit['evidence_turn_ids']).issubset({t['id'] for t in interview}):
            raise ValueError('Invalid audit evidence: ' + task['id'])
        luna = int(prediction['verdict'] == task['expected']['verdict'])
        if reward['verdict'] != prediction['verdict'] or reward['expected'] != task['expected']['verdict'] or reward['reward'] != luna:
            raise ValueError('Stale Harbor reward: ' + task['id'])
        astra = audit['score']
        if type(astra) is not int or astra not in (0, 1):
            raise ValueError('Interview score must be 0 or 1')
        output.append({**audit, 'luna_score': luna, 'astra_score': astra, 'overall_score': (luna + astra) / 2})
    n = len(output)
    if not n:
        raise ValueError('No cases to evaluate')
    return {'version': rubric['version'], 'method': rubric['authorship'], 'weights': {'luna': 0.5, 'astra': 0.5},
            'luna_score': sum(t['luna_score'] for t in output) / n,
            'astra_score': sum(t['astra_score'] for t in output) / n,
            'overall_score': sum(t['overall_score'] for t in output) / n, 'tasks': output}


def main():
    # Reuse the verdict evaluator's full source-evidence freshness checks.
    eval_harbor.load_cases(eval_harbor.DEFAULT_RESULTS, eval_harbor.DEFAULT_DECISIONS)
    data = ROOT / 'public/data'
    results = json.loads((data / 'results.json').read_text())
    decisions = json.loads((data / 'decisions.json').read_text())
    path = data / 'harbor-summary.json'
    harbor = json.loads(path.read_text())
    rubric = json.loads((ROOT / 'interview_rubric.json').read_text())
    harbor['workflow'] = evaluate(results, decisions, harbor, rubric)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(harbor, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)
    for name in ('luna_score', 'astra_score', 'overall_score'):
        print(f"{name}: {harbor['workflow'][name]:.1%}")
    print('Interview scores are retrospective assistant audit ratings, not automated semantic grading.')


if __name__ == '__main__':
    main()
