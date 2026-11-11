#!/usr/bin/env python3
"""Every number and claim on every customer surface equals the run summary (C7) and the release verdict.

    check_customer_numbers.py <run dir> [--chat <final message .md>] [--report <report.html>]

Reads the run dir's `run.json` (C7) and `release_verdict.json`, then each surface: the chat's final message (when
given), `analytics.md`, the report page's embedded data (when given) and both banners. Prints one JSON line per
finding `{surface, code, said, expected}` and exits 1 on any; 0 when every surface agrees; 2 when the run dir is
unreadable.

What it asserts, surface by surface:
- the run summary itself: `valid_trials + invalid_trials` = the trials drawn, `invalid_trials` = `ours_infra +
  harness_fault + provider_infra + agent_crash`, `passed_valid <= valid_trials` (`c7_incoherent`); absent →
  `c7_missing` and no number is checked;
- every "P of V … passed" equals `passed_valid` of `valid_trials` (`headline`) — on a multi-trial run only the pass
  line's conversation clause (`trials` / `conversations`): its "N of M scenarios passed" clause is the scores block's
  scenario pair, and one that differs from `run_numbers.scenario_passes` is a WARNING on stderr (`scenario_passes`),
  never a finding, so it never fails a publish; every "N of M … could not run on our
  side", "N of M … stopped on the agent's model provider" and "N of M … could not start on the agent's side" equals
  that side's invalid trials of the trials drawn (`could_not_run`, `provider_outage`, `agent_side`);
- the `Scenarios — N reliable · …` line equals the line `run_numbers.scenarios_line` prints from this run.json
  (`scenarios_line`);
- every pinned-regression line equals the line `render_run_report` renders from this run.json (`regression_line`);
- every trials-table label is the one `render_run_report.short_labels` gives that id (`scenario_label`);
- no surface says a trial "errored and scored nothing" / "(not scored)", counts an invalid trial against the agent,
  or shows a judge's vote share (`errored_not_scored`, `invalid_counted`, `vote_share`);
- no banner says "no result either way" of a check this run's run.json scored (`untested_but_scored`);
- the report page's copy of this run's `run.json` shows the run's `scores` as `run.json` on disk carries them
  (simulation-report 0.56.1 headlines a run off that SDK block): the agent verdict, its blocking critical failures and
  unscored scenarios, the cutoff, the suite's score and result, and each scenario's and conversation's score, result
  and status — none coerced to the page schema's default (`scripts/report/schema.json`), none missing, none added
  (`report_scores`); and the verdict it shows is the one `release_verdict.json` copied from the same block
  (`report_verdict`). The page's `reasons` are its explanation, not a number: a reason its schema lacks is the fill's
  misread, counted in the hand-off's integrity line.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / 'skills' / 'simulation-run' / 'scripts'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import render_run_report as rr  # noqa: E402
import run_numbers  # noqa: E402

_PASSED_NOUN = r'(?:\*\*)?(\d+) of (\d+)(?:\*\*)? (?:valid )?(?:{}) passed'
_PASSED_TABLE = r'|\| \*\*Passed\*\* \| \*\*(\d+) of (\d+)\*\*'
#: The C7 clause: every noun on a single-trial run (one clause, `run_numbers.pass_line`); on a multi-trial run the
#: conversation nouns only — "N of M scenarios passed" there is the scores block's scenario pair (:data:`_SCENARIO_PASSED`).
_PASSED = re.compile(_PASSED_NOUN.format('trials|scenarios|conversations') + _PASSED_TABLE)
_PASSED_MULTI = re.compile(_PASSED_NOUN.format('trials|conversations') + _PASSED_TABLE)
_SCENARIO_PASSED = re.compile(_PASSED_NOUN.format('scenarios'))
_COULD_NOT = re.compile(r'(\d+) of (\d+) (?:trials |scenarios |conversations )?could not run on our side')
_PROVIDER = re.compile(r"(\d+) of (\d+) (?:trials |scenarios |conversations )?stopped on the agent's model provider")
_AGENT_SIDE = re.compile(r"(\d+) of (\d+) (?:trials |scenarios |conversations )?could not start on the agent's side")
_BANNED = (
    (re.compile(r'errored and scored nothing|errored \(not scored\)', re.IGNORECASE), 'errored_not_scored'),
    (re.compile(r'invalid (?:trials? )?counted as not passed|an invalid trial counts as not passed', re.IGNORECASE), 'invalid_counted'),
    (re.compile(r'\d+% of (?:its|the) votes', re.IGNORECASE), 'vote_share'),
)
_SCENARIOS = re.compile(r'^Scenarios — .+? trials each\)', re.MULTILINE)
_REGRESSION = re.compile(r'^\s*- \*\*(?P<title>.+?)\*\* — ')
_LABEL_ROW = re.compile(r'^\| (?P<label>[^|`]+?) \| `(?P<sid>[^`]+)` \|')
_UNTESTED = re.compile(r'Not tested here: (?:critical safety check )?(?P<name>[^:]+): this run could not set up')
_REPORT_DATA = re.compile(r'<script\b[^>]*\bid="plurai-report-data"[^>]*>(.*?)</script>', re.DOTALL)
#: What the page shows of a conversation in the run's `scores`.
_CONVERSATION_SHOWN = ('trial', 'trial_dir', 'status', 'score', 'result')
_CRITICAL_SHOWN = ('scenario', 'trial', 'eval')
#: The vendored page's schema map: the enums it reads each shown value against (a value outside one it shows as its
#: default, `unknown`).
PAGE_SCHEMA = HERE / 'report' / 'schema.json'


def _finding(surface: str, code: str, said: object, expected: object) -> dict:
    return {'surface': surface, 'code': code, 'said': said, 'expected': expected}


def check_c7(run: dict) -> tuple[dict | None, list[dict]]:
    """The C7 counts and the findings on the run summary itself."""
    try:
        counts = run_numbers.c7(run)
    except run_numbers.C7Missing as exc:
        return None, [_finding('run.json', 'c7_missing', str(exc), 'totals.valid_trials/invalid_trials/passed_valid/invalid_by_cause')]
    totals, found = run['totals'], []
    expected_drawn = totals.get('conversations') or totals.get('scenarios')
    if isinstance(expected_drawn, int) and counts['valid_trials'] + counts['invalid_trials'] != expected_drawn:
        found.append(_finding('run.json', 'c7_incoherent', counts['valid_trials'] + counts['invalid_trials'], expected_drawn))
    if counts['invalid_trials'] != sum(counts['invalid_by_cause'].values()):
        found.append(_finding('run.json', 'c7_incoherent', f'invalid_trials {counts["invalid_trials"]}',
                              f'{" + ".join(run_numbers.INVALID_CAUSES)} = {sum(counts["invalid_by_cause"].values())}'))
    if counts['passed_valid'] > counts['valid_trials']:
        found.append(_finding('run.json', 'c7_incoherent', f'passed_valid {counts["passed_valid"]}', f'<= {counts["valid_trials"]}'))
    return counts, found


def check_text(surface: str, text: str, run: dict, counts: dict | None) -> list[dict]:
    """Every number, regression line, label and banned phrase on one surface's text."""
    found = [_finding(surface, code, m.group(0), None) for pattern, code in _BANNED for m in pattern.finditer(text)]
    if counts is not None:
        want = (counts['passed_valid'], counts['valid_trials'])
        multi = run_numbers.trials_each(run) > 1
        for m in (_PASSED_MULTI if multi else _PASSED).finditer(text):
            said = tuple(int(g) for g in m.groups() if g is not None)
            if said != want:
                found.append(_finding(surface, 'headline', m.group(0), f'{want[0]} of {want[1]}'))
        pair = run_numbers.scenario_passes(run) if multi else None
        for m in _SCENARIO_PASSED.finditer(text) if pair is not None else ():
            if pair is not None and (int(m.group(1)), int(m.group(2))) != pair:  # warn, never block: off the exit-1 findings
                print('WARNING: ' + json.dumps(_finding(surface, 'scenario_passes', m.group(0), f'{pair[0]} of {pair[1]}'), ensure_ascii=False),
                      file=sys.stderr)
        for pattern, key, code in (
            (_COULD_NOT, 'could_not_run', 'could_not_run'),
            (_PROVIDER, 'provider_outage', 'provider_outage'),
            (_AGENT_SIDE, 'agent_side', 'agent_side'),
        ):
            want = (counts[key], run_numbers.drawn(run))
            for m in pattern.finditer(text):
                if (int(m.group(1)), int(m.group(2))) != want:
                    found.append(_finding(surface, code, m.group(0), f'{want[0]} of {want[1]}'))
    expected_scenarios = run_numbers.scenarios_line(run)
    found += [_finding(surface, 'scenarios_line', m.group(0), expected_scenarios) for m in _SCENARIOS.finditer(text) if m.group(0) != expected_scenarios]
    rendered = {m.group('title'): line for line in rr.customer_regression_lines(run)[1:] if (m := _REGRESSION.match(line))}
    for line in text.splitlines():
        m = _REGRESSION.match(line)
        if m and m.group('title') in rendered and line.strip() != rendered[m.group('title')]:
            found.append(_finding(surface, 'regression_line', line.strip(), rendered[m.group('title')]))
    labels = rr.short_labels(sorted(((run.get('trials') or {}).get('scenarios') or {})))
    for line in text.splitlines():
        m = _LABEL_ROW.match(line)
        if m and m.group('sid') in labels and m.group('label').strip() != labels[m.group('sid')]:
            found.append(_finding(surface, 'scenario_label', f'{m.group("label").strip()} = {m.group("sid")}', labels[m.group('sid')]))
    return found


def check_banners(verdict: dict, run: dict) -> list[dict]:
    """No banner says "no result either way" of a check this run scored; no banner shows a vote share."""
    decided = {str(e.get('id')): (e.get('passed') or 0) + (e.get('failed') or 0) for e in run.get('evals') or [] if isinstance(e, dict)}
    policy_of = {str(r.get('display_name')): str(r.get('policy')) for r in verdict.get('named_risks') or [] if isinstance(r, dict)}
    found = []
    for key in ('customer_banner', 'banner'):
        text = verdict.get(key)
        if not isinstance(text, str):
            continue
        found += [_finding(key, code, m.group(0), None) for pattern, code in _BANNED for m in pattern.finditer(text)]
        for m in _UNTESTED.finditer(text):
            policy = policy_of.get(m.group('name').strip(), m.group('name').strip())
            if decided.get(policy):
                found.append(_finding(key, 'untested_but_scored', m.group(0), f'{policy} decided {decided[policy]} cell(s)'))
    return found


def _shown_scores(scores: object) -> dict | None:
    """The numbers and claims the page shows off a run's `scores` block (see the module docstring); None without one."""
    if not isinstance(scores, dict):
        return None

    def part(key: str) -> dict:
        return scores.get(key) if isinstance(scores.get(key), dict) else {}

    agent, suite = part('agent'), part('suite')
    scenarios = part('scenarios')
    return {
        'cutoff': scores.get('cutoff'),
        'suite': {k: suite.get(k) for k in ('score', 'result')},
        'agent': {'verdict': agent.get('verdict'), 'unscored_scenarios': agent.get('unscored_scenarios'),
                  'blocking_critical': [{k: row.get(k) for k in _CRITICAL_SHOWN} for row in agent.get('blocking_critical') or []
                                        if isinstance(row, dict)]},
        'scenarios': {sid: {'score': row.get('score'), 'result': row.get('result'),
                            'conversations': [{k: c.get(k) for k in _CONVERSATION_SHOWN} for c in row.get('conversations') or []
                                              if isinstance(c, dict)]}
                      for sid, row in scenarios.items() if isinstance(row, dict)},
    }


def _enum_misses(value: object, schema: object, at: str = 'scores') -> list[tuple[str, object, list]]:
    """`(field, value, enum)` for each value the page's schema reads against an enum it is not in."""
    if not isinstance(schema, dict):
        return []
    branches = [b for b in schema.get('anyOf') or [] if isinstance(b, dict)]
    if branches:
        fitting = [b for b in branches if b.get('type') not in ('null', None) or value is None]
        return min((_enum_misses(value, b, at) for b in fitting), key=len) if fitting else []
    if isinstance(schema.get('enum'), list) and value not in schema['enum']:
        return [(at, value, schema['enum'])]
    if isinstance(value, dict):
        props = schema.get('properties') if isinstance(schema.get('properties'), dict) else {}
        extra = schema.get('additionalProperties')
        return [miss for key, item in value.items() for miss in _enum_misses(item, props.get(key, extra), f'{at}.{key}')]
    if isinstance(value, list):
        return [miss for i, item in enumerate(value) for miss in _enum_misses(item, schema.get('items'), f'{at}[{i}]')]
    return []


def _page_scores_schema() -> dict | None:
    try:
        return json.loads(PAGE_SCHEMA.read_text(encoding='utf-8'))['artifacts/runs/*/run.json']['properties']['scores']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_report(html: str, run_dir: Path) -> list[dict]:
    """The report page's embedded copy of this run's run.json shows its `scores` as the file on disk carries them, and
    the agent verdict `release_verdict.json` copied. A page that does not show this run says nothing about it."""
    m = _REPORT_DATA.search(html)
    try:
        files = json.loads(m.group(1).replace('<\\/', '</'))['files'] if m else None
    except (ValueError, KeyError, TypeError):
        files = None
    if not isinstance(files, dict):
        return [_finding('report.html', 'report_scores', 'no embedded report data', 'the run summary')]
    page = files.get(f'artifacts/runs/{run_dir.name}/run.json')
    if not isinstance(page, dict):
        return []  # the page shows no Run for this run
    said = _shown_scores(page.get('scores'))
    want = _shown_scores(json.loads((run_dir / 'run.json').read_text(encoding='utf-8')).get('scores'))
    found = [] if said == want else [_finding('report.html', 'report_scores', said, want)]
    found += [_finding('report.html', 'report_scores', f'{field}: {value!r}', f'one of {enum}')
              for field, value, enum in _enum_misses(said, _page_scores_schema()) if said is not None]
    verdict = run_dir / 'release_verdict.json'
    agent = json.loads(verdict.read_text(encoding='utf-8')).get('agent') if verdict.is_file() else None
    if isinstance(agent, dict):
        shown = (said or {}).get('agent', {}).get('verdict')
        if shown != agent.get('verdict'):
            found.append(_finding('report.html', 'report_verdict', shown, agent.get('verdict')))
    return found


def check(run_dir: Path, chat: str | None = None, report: str | None = None) -> list[dict]:
    run = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
    counts, found = check_c7(run)
    if chat is not None:
        found += check_text('chat', chat, run, counts)
    if (run_dir / 'analytics.md').is_file():
        found += check_text('analytics.md', (run_dir / 'analytics.md').read_text(encoding='utf-8'), run, counts)
    if (run_dir / 'release_verdict.json').is_file():
        found += check_banners(json.loads((run_dir / 'release_verdict.json').read_text(encoding='utf-8')), run)
    if report is not None:
        found += check_report(report, run_dir)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--chat', type=Path, help="the final chat message (markdown)")
    parser.add_argument('--report', type=Path, help='the filled report.html')
    args = parser.parse_args(argv)
    try:
        found = check(args.run_dir, args.chat.read_text(encoding='utf-8') if args.chat else None,
                      args.report.read_text(encoding='utf-8') if args.report else None)
    except (OSError, ValueError) as exc:
        print(f'check_customer_numbers: {exc}', file=sys.stderr)
        return 2
    for finding in found:
        print(json.dumps(finding, ensure_ascii=False))
    return 1 if found else 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
