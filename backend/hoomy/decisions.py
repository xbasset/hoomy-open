"""Typed Decisions adapter. Credentials stay on the host worker."""
import ast
import http.client
import json
import math
import ssl
import time
from pathlib import Path

MODEL = 'gpt-6-luna'
QUESTION_VERSION = 'ready-ideas-v1'
CHOICE_TIMEOUT = 5
QUESTION_TIMEOUT = 8
FAILURE_CODES = {'timeout', 'connection', 'rate_limit', 'access', 'provider', 'invalid_request',
                 'invalid_response', 'input_budget', 'configuration', 'worker_error'}


class DecisionError(Exception):
    def __init__(self, message, code='invalid_response', http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def failure_details(error):
    # Only classified metadata crosses into logs and persisted job state.
    code = error.code if isinstance(error, DecisionError) and error.code in FAILURE_CODES else 'worker_error'
    result = {'code': code}
    if isinstance(error, DecisionError) and type(error.http_status) is int and 400 <= error.http_status <= 599:
        result['http_status'] = error.http_status
    return result


def read_key(path):
    # Read only the named secret; never source a dotenv file as executable code.
    for line in Path(path).read_text().splitlines():
        name, sep, value = line.removeprefix('export ').partition('=')
        if sep and name.strip() == 'OPENAI_API_KEY':
            value = value.strip()
            if value.startswith(('"', "'")):
                value = ast.literal_eval(value)
            else:
                value = value.split(' #', 1)[0].strip()
            if isinstance(value, str) and value and '\n' not in value:
                return value
    raise DecisionError('OPENAI_API_KEY is missing from the supplied file.', 'configuration')


def probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def validate(body, questions):
    answers = body.get('answers')
    if body.get('model') != MODEL or not isinstance(answers, list) or len(answers) != len(questions):
        raise DecisionError('Unexpected decision response shape or model.')
    for question, answer in zip(questions, answers):
        if not isinstance(answer, dict) or answer.get('name') != question['name']:
            raise DecisionError('Decision answer identity mismatch.')
        if answer.get('type') == 'refusal':
            continue
        if answer.get('type') != question['type'] or not probability(answer.get('confidence')):
            raise DecisionError('Invalid decision type or confidence.')
        options = question.get('choices', question.get('levels', []))
        allowed = [x.get('value', x.get('label')) for x in options]
        distribution = answer.get('probabilities')
        key = 'value' if question['type'] == 'choice' else 'label'
        if (not isinstance(distribution, list) or len(distribution) != len(allowed)
                or {x.get(key) for x in distribution} != set(allowed)
                or any(not probability(x.get('probability')) for x in distribution)
                or abs(sum(x['probability'] for x in distribution)-1) > .01):
            raise DecisionError('Invalid decision distribution.')
        if question['type'] == 'choice' and answer.get('choice') not in allowed:
            raise DecisionError('Unexpected decision choice.')
        if question['type'] == 'score':
            score = answer.get('score')
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= len(allowed)-1:
                raise DecisionError('Invalid decision score.')
    return {a['name']: a for a in answers}


class DecisionsClient:
    def __init__(self, key, timeout=5):
        self.key = key
        context = ssl.create_default_context()
        if not ssl.get_default_verify_paths().cafile and Path('/etc/ssl/cert.pem').exists():
            context.load_verify_locations('/etc/ssl/cert.pem')
        self.connection = http.client.HTTPSConnection('api.openai.com', timeout=timeout, context=context)
        self.last_used = None

    def close(self):
        self.connection.close()

    def evaluate(self, evidence, questions):
        payload = json.dumps({'model': MODEL, 'input': json.dumps(evidence, ensure_ascii=False),
                              'questions': questions}, ensure_ascii=False).encode()
        if len(payload) > 64_000:
            raise DecisionError('Experiment input budget exceeded.', 'input_budget')
        started = time.monotonic()
        if self.last_used is not None and started - self.last_used > 30:
            self.close()
        try:
            self.connection.request('POST', '/v1/decisions', body=payload,
                headers={'Authorization': 'Bearer '+self.key, 'Content-Type': 'application/json'})
            response = self.connection.getresponse()
            raw = response.read(512_001)
            if response.status != 200:
                # No provider body, request content or key in logs; no automatic retries.
                code = ('rate_limit' if response.status == 429 else 'access' if response.status in (401, 403)
                        else 'provider' if response.status >= 500 else 'invalid_request')
                raise DecisionError(f'Decisions returned HTTP {response.status}.', code, response.status)
            if len(raw) > 512_000:
                raise DecisionError('Decision response budget exceeded.')
            body = json.loads(raw)
            answers = validate(body, questions)
            return {'answers': answers, 'usage': body.get('usage', {}),
                    'model': body['model'], 'question_version': QUESTION_VERSION,
                    'elapsed_ms': round((time.monotonic()-started)*1000, 2)}
        except DecisionError:
            self.close()
            raise
        except (OSError, ValueError, http.client.HTTPException) as error:
            self.close()
            code = 'timeout' if isinstance(error, TimeoutError) else 'invalid_response' if isinstance(error, ValueError) else 'connection'
            raise DecisionError('Decision transport or decoding failed: '+type(error).__name__, code) from None
        finally:
            self.last_used = time.monotonic()
