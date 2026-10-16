"""Deterministic eligibility cannot be overruled by a model score."""
from datetime import datetime


def instant(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timestamp requires a timezone.')
    return value


def gate(idea, observations, now, attention_minutes):
    """Inputs are trusted stored envelopes, not fields invented by Decisions."""
    now = instant(now)
    if idea.get('archived'):
        return 'archived'
    current = [o for o in observations if o['idea_id'] == idea['id']
               and o['idea_version'] == idea['version'] and not o.get('superseded')]
    # A terminal completion is sticky for this version; new sources cannot silently reopen it.
    if any(o['state'] == 'completed' for o in current):
        return 'completed'
    valid = [o for o in current if o.get('source') in ('user_confirmation', 'direct_observation')
             and o.get('evidence_ref') and o.get('supports_exact_claim') is True
             and o.get('valid_until') and instant(o['observed_at']) <= now < instant(o['valid_until'])]
    if {o['state'] for o in valid} >= {'pending', 'unknown'} or any(o['state'] == 'conflicting' for o in valid):
        return 'needs_update'
    if not any(o['state'] == 'pending' for o in valid):
        return 'needs_update'
    if idea.get('preconditions') != 'satisfied':
        return 'blocked'
    if idea['attention_minutes'] > attention_minutes:
        return 'outside_budget'
    return 'eligible'


def route(eligibility, answers):
    if eligibility != 'eligible':
        return eligibility
    if any(a.get('type') == 'refusal' for a in answers.values()):
        return 'abstain'
    # Established state is authoritative; do not ask again because a classifier is uncertain.
    # Context thresholds remain experimental and require labeled daily-use evaluation.
    fit = answers.get('context_fit', {})
    if fit.get('choice') != 'fits' or fit.get('confidence', 0) < .8:
        return 'abstain'
    if answers.get('usefulness', {}).get('score', 0) < 2:
        return 'abstain'
    return 'suggest'


def select(ideas, observations, now, attention_minutes, answer, expected_versions):
    """Recheck current state at delivery, including changes while the API was running."""
    if answer.get('type') != 'choice' or answer.get('choice') == 'abstain':
        return None
    chosen = next((i for i in ideas if i['id'] == answer.get('choice')), None)
    if chosen is None or expected_versions.get(chosen['id']) != chosen['version']:
        return None
    if gate(chosen, observations, now, attention_minutes) != 'eligible':
        return None
    return chosen['id']
