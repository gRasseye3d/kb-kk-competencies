"""Pure snapshot adapters and allowlisted projections. Never rewrite legacy JSON."""
from copy import deepcopy


PRIMARY_RULE = 'primary_exact_match_v1'


def snapshot_format(snapshot):
    # Only absence means legacy: bool, floats, strings and explicit null are
    # not format identifiers, even where Python would compare them equal to 1/2.
    version = snapshot.get('format_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unsupported snapshot format')
    return version


def blocks(snapshot):
    if snapshot_format(snapshot) == 2:
        return snapshot['blocks']
    grouped = {}
    for comp in snapshot.get('competencies', []):
        bid = comp.get('block_id')
        if bid not in grouped:
            grouped[bid] = {'block_id': bid, 'title': 'Основной блок',
                            'position': len(grouped) + 1, 'competencies': []}
        grouped[bid]['competencies'].append(comp)
    return list(grouped.values())


def competencies(snapshot):
    return [dict(comp, block_id=block.get('block_id'))
            for block in blocks(snapshot) for comp in block['competencies']]


def visible_question(question):
    return {key: question[key] for key in ('id', 'body', 'type')} | {
        'options': [{'id': option['id'], 'body': option['body']} for option in question['options']]}


def public_blocks(snapshot, included=None, show_questions=True, names=None):
    result = []
    names = names or {}
    for block in blocks(snapshot):
        comps = []
        for comp in block['competencies']:
            cid = str(comp['competency_id'])
            if included is not None and cid not in included:
                continue
            comps.append({'competency_id': cid,
                          'competency_name': comp.get('competency_name', names.get(cid, 'Компетенция')),
                          'questions': [visible_question(q) for q in comp['questions']] if show_questions else []})
        if comps:
            result.append({'title': block['title'], 'position': block['position'], 'competencies': comps})
    return result


def for_attempt(snapshot, included):
    # Legacy snapshots are preserved exactly. New snapshots contain included blocks only;
    # excluded competency metadata remains in attempt_competencies.
    version = snapshot_format(snapshot)
    result = deepcopy(snapshot)
    if version == 2:
        result['blocks'] = [dict(block, competencies=[comp for comp in block['competencies']
                           if str(comp['competency_id']) in included]) for block in result['blocks']]
        result['blocks'] = [block for block in result['blocks'] if block['competencies']]
    return result


def scoring(snapshot):
    """Dispatch explicitly; missing rule is permitted only in legacy snapshots."""
    version = snapshot_format(snapshot)
    rule = snapshot.get('scoring_rule')
    if rule is None and version == 1:
        rule = PRIMARY_RULE
    if rule != PRIMARY_RULE:
        raise ValueError('Unsupported scoring rule')
    return exact_match, primary_level


def exact_match(selected, question):
    return bool(selected) and set(selected) == {o['id'] for o in question['options'] if o['correct']}


def primary_level(correct, count):
    return 0 if correct == 0 else (2 if correct == count else 1)
