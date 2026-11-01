"""Ask Astra to localize visible damage; preserve original images and verdicts."""
import base64
import hashlib
import json
import math
import pipeline


def validate_annotations(value):
    if not isinstance(value, dict) or not isinstance(value.get('regions'), list) or len(value['regions']) > 6:
        raise ValueError('Expected up to six damage regions')
    if not isinstance(value.get('uncertainty'), str):
        raise ValueError('Missing visual uncertainty')
    for region in value['regions']:
        if not isinstance(region.get('label'), str) or not region['label'].strip():
            raise ValueError('Missing region label')
        box = region.get('box')
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValueError('Invalid box')
        x, y, width, height = box
        if min(x, y) < 0 or min(width, height) <= 0 or x + width > 1 or y + height > 1:
            raise ValueError('Box outside image')
    return value


def annotate_tasks(data, key):
    for task in data['tasks']:
        if not task.get('image'):
            continue
        raw = (pipeline.PUBLIC / task['image'].lstrip('/')).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        saved = task.get('damage_annotations', {})
        if saved.get('source_image') == task['image'] and saved.get('image_sha256') == digest and saved.get('model') == pipeline.ADJUDICATOR_MODEL:
            validate_annotations(saved)
            continue
        response = pipeline.call_agent(pipeline.ADJUDICATOR_MODEL,
            'Inspect only the supplied insurance evidence image, which may show a vehicle, household belongings, or property. Locate visible damage or visible signs of water exposure. '
            'Do not infer hidden damage, cause, fault, intent, ownership, value, or insurance coverage. '
            'Return JSON with regions (at most six objects with a short label and box [x,y,width,height]) '
            'and uncertainty (a short string explaining visual limitations). Coordinates are fractions of the entire image '
            'from its top-left, between 0 and 1. Use tight boxes around visible findings, not the whole object or room. '
            'Use an empty regions list if no damage can be localized reliably. No Markdown.',
            [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(raw).decode()}]}],
            key, max_tokens=1200)
        value = validate_annotations(pipeline.parse_json_object(response, 'Damage localization', key))
        value.update(model=pipeline.ADJUDICATOR_MODEL, source_image=task['image'], image_sha256=digest)
        task['damage_annotations'] = value
        pipeline.write_results(data)
        print(task['id'], len(value['regions']), 'regions saved', flush=True)


def main():
    lock = pipeline.acquire_run_lock()
    try:
        data = json.loads(pipeline.RESULTS.read_text())
        annotate_tasks(data, pipeline.load_api_key())
    finally:
        pipeline.release_run_lock(lock)


if __name__ == '__main__':
    main()
