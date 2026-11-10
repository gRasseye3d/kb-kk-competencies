"""Strict interim import format. Never normalize or mutate a stored source payload."""
from collections import Counter
from decimal import Decimal
import json
import math
import uuid

from . import access, db
from .router import unique_object


def safe_json(value, max_depth=20):
    """Reject JSONB-incompatible text, deep structures and numbers unsafe to serialize.

    Iterative to avoid Python recursion on attacker-controlled JSON. PostgreSQL's
    JSONB->Python decoding uses float by default, so values beyond 1e308 would
    become Infinity on read and break the API's JSON response.
    """
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, str):
            if '\x00' in current:
                raise ValueError('Unsupported JSON Unicode character')
            try:
                current.encode('utf-8')
            except UnicodeError as exc:
                raise ValueError('Unsupported JSON Unicode character') from exc
        elif isinstance(current, dict):
            if depth >= max_depth:
                raise ValueError('JSON nesting exceeds 20 levels')
            for key, child in current.items():
                if not isinstance(key, str):
                    raise ValueError('JSON object keys must be strings')
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            if depth >= max_depth:
                raise ValueError('JSON nesting exceeds 20 levels')
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, bool) or current is None:
            continue
        elif isinstance(current, int):
            if current.bit_length() > 1024 or len(str(abs(current))) > 309:
                raise ValueError('JSON number too large')
        elif isinstance(current, (float, Decimal)):
            maximum, minimum = ((1e308, 1e-308) if isinstance(current, float)
                                else (Decimal('1e308'), Decimal('1e-308')))
            if (not math.isfinite(current) or abs(current) > maximum
                    or (current and abs(current) < minimum)):
                raise ValueError('JSON number too large or nonfinite')
        else:
            raise ValueError('Invalid JSON value')


def parse(text):
    if not isinstance(text, str):
        raise ValueError('JSON text required')
    try:
        length = len(text.encode('utf-8'))
    except UnicodeError as exc:
        raise ValueError('Invalid Unicode in JSON text') from exc
    if length > 512 * 1024:
        raise ValueError('JSON text exceeds 512 KiB')
    try:
        def integer(value):
            if len(value.lstrip('-')) > 309:
                raise ValueError('JSON number too large')
            return int(value)

        def decimal(value):
            result = Decimal(value)
            if len(value) > 1000 or abs(result.adjusted()) > 308:
                raise ValueError('JSON number too large')
            return result

        payload = json.loads(text, object_pairs_hook=unique_object, parse_int=integer,
                             parse_float=decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite number')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid JSON or duplicate JSON key') from exc
    safe_json(payload)
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get('questions'), list):
        items = payload['questions']
    else:
        raise ValueError('Expected JSON array or {questions: array}')
    if not 1 <= len(items) <= 200:
        raise ValueError('Expected 1 to 200 questions')
    return payload, items


def uuid_value(value):
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def validate(conn, did, user, item, duplicate=False):
    errors = []
    if not isinstance(item, dict):
        return ['Question must be an object']
    external = item.get('external_id')
    if not isinstance(external, str) or not external.strip() or len(external) > 128:
        errors.append('external_id must be a nonempty string (max 128)')
    if duplicate:
        errors.append('Duplicate external_id in import')
    code = item.get('competency_code')
    if not isinstance(code, str) or not code.strip():
        errors.append('competency_code required')
    elif not db.query('''SELECT 1 FROM competencies c JOIN profile_competencies pc ON pc.competency_id=c.id
        JOIN competency_profiles p ON p.id=pc.profile_id
        WHERE c.code=%s AND c.is_active AND p.department_id=%s AND p.is_active AND p.status='approved' ''',
        (code, did), one=True, conn=conn):
        errors.append('competency_code outside approved active department profile')
    question = item.get('question')
    body = item.get('body')
    if question is not None and body is not None:
        errors.append('Use question or body, not both')
    value = question if question is not None else body
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 5000:
        errors.append('question/body must be nonempty text (max 5000)')
    kind = item.get('type')
    if kind not in ('single', 'multiple'):
        errors.append('type must be single or multiple')
    options = item.get('options')
    if not isinstance(options, list) or not 2 <= len(options) <= 20:
        errors.append('options must contain 2 to 20 entries')
    else:
        ids, correct = [], 0
        for pos, option in enumerate(options, 1):
            if not isinstance(option, dict):
                errors.append(f'option {pos} must be an object')
                continue
            oid = option.get('id')
            if not isinstance(oid, str) or not oid.strip() or len(oid) > 128:
                errors.append(f'option {pos} id required (max 128)')
            else:
                ids.append(oid)
            if 'text' in option and 'body' in option:
                errors.append(f'option {pos} use text or body, not both')
            text = option.get('text', option.get('body'))
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 1000:
                errors.append(f'option {pos} text/body required (max 1000)')
            if 'is_correct' in option and 'correct' in option:
                errors.append(f'option {pos} use is_correct or correct, not both')
            flag = option.get('is_correct', option.get('correct'))
            if type(flag) is not bool:
                errors.append(f'option {pos} correct flag must be boolean')
            elif flag:
                correct += 1
        if len(ids) != len(set(ids)):
            errors.append('Duplicate option id')
        if correct < 1 or (kind == 'single' and correct != 1):
            errors.append('Invalid answer key')
    explanation = item.get('explanation')
    if explanation is not None and (not isinstance(explanation, str) or len(explanation) > 5000):
        errors.append('explanation must be text (max 5000)')
    page_id, rev_id = item.get('source_page_id'), item.get('source_revision_id')
    if rev_id is not None and page_id is None:
        errors.append('source_page_id required with source_revision_id')
    if page_id is not None:
        pid = uuid_value(page_id)
        if not pid:
            errors.append('Invalid source_page_id')
        else:
            page = db.query('''SELECT p.*, u.department_id AS author_department FROM pages p
                LEFT JOIN users u ON u.id=p.created_by WHERE p.id=%s FOR SHARE OF p''',
                (pid,), one=True, conn=conn)
            if not page or str(page['author_department']) != str(did) or not access.page(user, page, conn=conn):
                errors.append('Source page unavailable in department')
            elif not page['current_version']:
                errors.append('Source page has no revision')
            elif rev_id is not None:
                rid = uuid_value(rev_id)
                if not rid or not db.query('''SELECT 1 FROM page_revisions
                    WHERE id=%s AND page_id=%s AND version=%s''', (rid, pid, page['current_version']), one=True, conn=conn):
                    errors.append('Source revision is not current for page')
    return errors


def duplicates(items):
    counts = Counter(i['external_id'] for i in items if isinstance(i, dict)
                     and isinstance(i.get('external_id'), str) and i['external_id'].strip())
    return {key for key, count in counts.items() if count > 1}
