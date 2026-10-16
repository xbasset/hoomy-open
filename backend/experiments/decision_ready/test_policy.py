import copy
import json
import unittest
from decisions import MODEL, QUESTIONS, DecisionError, validate
from fixtures import BASE, NOW, cases
from policy import gate, route, select


class ReadyPolicyTests(unittest.TestCase):
    def test_regression_cases_and_untrusted_model_cannot_override_gate(self):
        confident = {'completion': {'type': 'choice', 'choice': 'pending', 'confidence': 1},
                     'context_fit': {'type': 'choice', 'choice': 'fits', 'confidence': 1},
                     'usefulness': {'type': 'score', 'score': 3, 'confidence': 1}}
        for case in cases():
            with self.subTest(case=case['name']):
                value = case['input']
                state = gate(value['idea'], value['observations'], NOW, 5)
                self.assertEqual(state, case['expected_gate'])
                self.assertEqual(route(state, confident), 'suggest' if state == 'eligible' else state)

    def test_explicit_pending_does_not_require_another_model_confirmation(self):
        answers = {'completion': {'type': 'choice', 'choice': 'unknown', 'confidence': .5},
                   'context_fit': {'type': 'choice', 'choice': 'fits', 'confidence': .95},
                   'usefulness': {'type': 'score', 'score': 2.2}}
        self.assertEqual(route('eligible', answers), 'suggest')

    def test_completion_is_sticky_after_expiry(self):
        value = copy.deepcopy(BASE)
        value['observations'][0].update(state='completed', valid_until='2026-10-06T09:00:00Z')
        self.assertEqual(gate(value['idea'], value['observations'], NOW, 5), 'completed')

    def test_unknown_and_pending_conflict_and_supersession(self):
        value = copy.deepcopy(BASE)
        value['observations'].append(dict(value['observations'][0], id='obs-2', state='unknown'))
        self.assertEqual(gate(value['idea'], value['observations'], NOW, 5), 'needs_update')
        value['observations'][1]['superseded'] = True
        self.assertEqual(gate(value['idea'], value['observations'], NOW, 5), 'eligible')

    def test_late_selection_cannot_revive_a_completed_or_changed_idea(self):
        value = copy.deepcopy(BASE)
        answer = {'type': 'choice', 'choice': value['idea']['id']}
        versions = {value['idea']['id']: 1}
        self.assertEqual(select([value['idea']], value['observations'], NOW, 5, answer, versions), value['idea']['id'])
        value['observations'][0]['state'] = 'completed'
        self.assertIsNone(select([value['idea']], value['observations'], NOW, 5, answer, versions))
        value['observations'][0]['state'] = 'pending'
        value['idea']['version'] = 2
        self.assertIsNone(select([value['idea']], value['observations'], NOW, 5, answer, versions))
        self.assertIsNone(select([value['idea']], value['observations'], NOW, 5, dict(answer, choice='invented'), versions))

    def test_refusal_and_partial_answer_abstain(self):
        self.assertEqual(route('eligible', {'context_fit': {'type': 'refusal'}}), 'abstain')
        self.assertEqual(route('eligible', {}), 'abstain')
        refusals = [{'type': 'refusal', 'name': q['name']} for q in QUESTIONS]
        self.assertEqual(len(validate({'model': MODEL, 'answers': refusals}, QUESTIONS)), 3)
        with self.assertRaises(DecisionError):
            validate({'model': MODEL, 'answers': refusals[:-1]}, QUESTIONS)
        with self.assertRaises(DecisionError):
            validate({'model': MODEL, 'answers': list(reversed(refusals))}, QUESTIONS)


if __name__ == '__main__':
    unittest.main()
