"""Run bounded synthetic calls; never read or write the pilot database."""
import argparse
import json
import statistics
from pathlib import Path
from decisions import DecisionsClient, DecisionError, read_key
from fixtures import cases
from policy import gate, route


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    client = DecisionsClient(read_key(args.key_file))
    rows = []
    failure = None
    try:
        for repetition in range(args.repeats):
            for case in cases():
                value = case['input']
                eligibility = gate(value['idea'], value['observations'], value['now'], value['attention_budget_minutes'])
                result = client.evaluate(value)
                routing = route(eligibility, result['answers'])
                row = {'case': case['name'], 'repeat': repetition+1, 'gate': eligibility, 'route': routing,
                       'expected_completion': case['expected_completion'], **result}
                rows.append(row)
                print(json.dumps({'case': case['name'], 'ms': result['elapsed_ms'], 'route': routing,
                                  'completion': result['answers']['completion'].get('choice')}), flush=True)
    except DecisionError as error:
        failure = str(error)
        print(failure, flush=True)
    finally:
        client.close()
        durations = [r['elapsed_ms'] for r in rows]
        summary = {'sample_count': len(rows), 'input_kind': 'fictional compact evidence; no live project data',
                   'protocol': 'serial HTTP requests, connection reused; all cases evaluated for classifier measurement even if locally ineligible',
                   'median_ms': statistics.median(durations) if durations else None,
                   'min_ms': min(durations) if durations else None, 'max_ms': max(durations) if durations else None,
                   'correct_completion_labels': sum(r['answers']['completion'].get('choice') == r['expected_completion'] for r in rows),
                   'ineligible_suggestions': sum(r['route']=='suggest' and r['gate']!='eligible' for r in rows),
                   'input_tokens': sum(r['usage'].get('input_tokens', 0) for r in rows),
                   'routing_policy': 'trusted-state-v2', 'failure': failure, 'trials': rows}
        Path(args.output).write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps({k:v for k,v in summary.items() if k!='trials'}), flush=True)
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
