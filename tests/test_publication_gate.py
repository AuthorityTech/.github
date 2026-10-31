import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / '.github/workflows/dependabot-automerge.yml').read_text()
SCRIPT = textwrap.dedent(SOURCE.split('      - name: Merge once the head commit is green\n', 1)[1].split('        run: |\n', 1)[1])
HEAD = 'a' * 40
BASE = 'b' * 40
REPO = 'Bramwell-Inc/jaxonparrott.com'
NAMES = ['agentic-verify', 'claims', 'portfolio-retired-pointers']

MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE'])
f = json.loads((root / 'fixture.json').read_text())
a = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log: log.write(json.dumps(a) + '\\n')
url = next((v for v in a if v.startswith('repos/')), '')
def output(value): print(json.dumps(value))
if a[0] != 'api': sys.exit('unexpected non-API command')
if '-X' in a and a[a.index('-X')+1] == 'PUT':
    if f.get('refuse_merge'):
        f['pr']['head']['sha'] = 'c'*40
        (root / 'fixture.json').write_text(json.dumps(f))
        sys.exit(1)
    assert '-f' in a and 'sha=' + 'a'*40 in a
    output({'sha': 'd'*40}); sys.exit(0)
if url.endswith('/dispatches'): sys.exit(0)
if '/rules/branches/' in url:
    for rule in f.get('rules', []):
        if rule['type'] == 'merge_queue': print('merge_queue')
elif '/files?' in url:
    if f.get('file_api_error'): sys.exit(1)
    output(f['files'])
elif '/pulls/' in url:
    count_file = root / 'reads'
    count = int(count_file.read_text()) if count_file.exists() else 0
    count_file.write_text(str(count+1))
    p = f['pr'] if count < 2 else f.get('after', f['pr'])
    if '--jq' in a: print(p['title'])
    else: output(p)
elif '/actions/runs?' in url:
    assert 'head_sha=' + 'a'*40 in url
    output([99])
elif '/check-runs?' in url:
    assert '/commits/' + 'a'*40 + '/' in url
    read_file = root / 'check_reads'
    count = int(read_file.read_text()) if read_file.exists() else 0
    read_file.write_text(str(count+1))
    runs = f.get('later_runs', f['runs']) if count else f['runs']
    output({'total_count': f.get('check_count', len(runs)), 'check_runs': runs})
elif '/status?' in url:
    assert '/commits/' + 'a'*40 + '/' in url
    output({'total_count': f.get('status_count', len(f['statuses'])), 'statuses': f['statuses']})
elif '/commits/' in url: output({'parents': [{'sha': 'b'*40}]})
elif url.count('/') == 2: print('main')
else: sys.exit('unexpected endpoint: ' + url)
'''

def fixture():
    return dict(
        pr=dict(state='open', merged=False, draft=False, title='Fixture', head=dict(sha=HEAD, ref='editorial/post', repo=dict(full_name=REPO)), base=dict(sha=BASE, ref='main'), changed_files=1),
        files=[dict(filename='content/posts/2026-10-31-essay.md', status='modified')],
        statuses=[],
        runs=[dict(name=name, conclusion='success', check_suite=dict(id=1)) for name in NAMES],
    )

class PublicationGate(unittest.TestCase):
    def run_gate(self, f=None, repo=REPO, enabled='true', kind='editorial', required=None, trigger=''):
        f = f or fixture()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'fixture.json').write_text(json.dumps(f))
            for name, script in [('gh', MOCK), ('sleep', '#!/bin/sh\nexit 0\n')]:
                p = root / name; p.write_text(script); p.chmod(0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'], FIXTURE=d, RUNNER_TEMP=d, GH_TOKEN='local-fixture-only', REPO=repo, PR='1', SHA=HEAD, KIND=kind, REQUIRED='\n'.join(required or ['Vercel', *NAMES]), CONTENT_WITHOUT_PREVIEW=enabled, WORKFLOW_REF=repo + '/.github/workflows/dependabot-automerge.yml@main', WAIT_MINUTES='0', TRIGGER_SUITE=trigger, BASE_REF='main')
            result = subprocess.run(['bash', '-c', SCRIPT], env=env, text=True, capture_output=True, timeout=10)
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            merged = any('-X' in c and 'PUT' in c for c in calls)
            return result, merged, calls

    def assert_held(self, f, **kwargs):
        result, merged, calls = self.run_gate(f, **kwargs)
        self.assertFalse(merged, result.stdout + result.stderr)
        return result, calls

    def test_content_and_portfolio_require_all_ci_without_preview(self):
        for path in ['content/posts/2026-10-31-essay.md', 'portfolio/retired-pages.json']:
            f = fixture(); f['files'][0]['filename'] = path
            r, merged, calls = self.run_gate(f)
            self.assertEqual(r.returncode, 0, r.stderr); self.assertTrue(merged, r.stdout)
            self.assertEqual(sum('/dispatches' in str(c) for c in calls), 1)

    def test_missing_failed_pending_or_skipped_required_check_holds(self):
        for name in NAMES:
            for state in ['missing', None, 'failure', 'cancelled', 'timed_out', 'skipped', 'neutral']:
                f = fixture()
                if state == 'missing': f['runs'] = [c for c in f['runs'] if c['name'] != name]
                else: next(c for c in f['runs'] if c['name'] == name)['conclusion'] = state
                with self.subTest(name=name, state=state): self.assert_held(f)

    def test_present_vercel_and_unrelated_checks_never_bypass_failure(self):
        for state in ['pending', 'failure', 'error']:
            f = fixture(); f['statuses'] = [dict(context='Vercel', state=state)]
            self.assert_held(f)
        for state in [None, 'failure', 'cancelled', 'action_required', 'startup_failure']:
            f = fixture(); f['runs'].append(dict(name='Vercel', conclusion=state, check_suite=dict(id=2)))
            self.assert_held(f)

    def test_platform_dependencies_and_unknown_paths_require_preview(self):
        for path in ['src/proxy.ts', 'vercel.json', 'package.json', 'package-lock.json', 'packages/schema.tgz', '.github/workflows/agentic-scan.yml', 'content/posts/code.mjs', 'content/posts/a/b.md', 'src/data/removed-posts.json', 'portfolio/retired-pointer-baseline.json', 'public/images/new.png']:
            f = fixture(); f['files'].append(dict(filename=path, status='modified')); f['pr']['changed_files'] = 2
            with self.subTest(path=path):
                self.assert_held(f)
                f['statuses'] = [dict(context='Vercel', state='success')]
                self.assertTrue(self.run_gate(f)[1])

    def test_empty_truncated_renamed_or_unstable_files_cannot_opt_out(self):
        variants = []
        f = fixture(); f['files'] = []; f['pr']['changed_files'] = 0; variants.append(f)
        f = fixture(); f['pr']['changed_files'] = 2; variants.append(f)
        f = fixture(); f['files'][0].update(status='renamed', previous_filename='package.json'); variants.append(f)
        f = fixture(); f['after'] = copy.deepcopy(f['pr']); f['after']['head']['sha'] = 'c'*40; variants.append(f)
        f = fixture(); f['after'] = copy.deepcopy(f['pr']); f['after']['base']['sha'] = 'c'*40; variants.append(f)
        f = fixture(); f['file_api_error'] = True; variants.append(f)
        for f in variants: self.assert_held(f)

    def test_moved_closed_draft_fork_wrong_base_wrong_branch_queue_hold(self):
        for field, value in [('draft', True), ('state', 'closed'), ('head.sha', 'c'*40), ('head.ref', 'fix/platform'), ('base.ref', 'other'), ('head.repo.full_name', 'fork/site')]:
            f = fixture(); obj = f['pr']; parts = field.split('.')
            for part in parts[:-1]: obj = obj[part]
            obj[parts[-1]] = value
            self.assert_held(f)
        f = fixture(); f['rules'] = [dict(type='merge_queue')]; self.assert_held(f)

    def test_other_callers_and_default_input_retain_status_dependency(self):
        self.assert_held(fixture(), enabled='false')
        for repo in ['AuthorityTech/website', 'AuthorityTech/christianlehman.com', 'Bramwell-Inc/machinerelations.ai', 'Bramwell-Inc/paralabs.ai', 'Bramwell-Inc/paralax.ai']:
            for enabled in ['false', 'true']:
                f = fixture(); f['pr']['head']['repo']['full_name'] = repo
                r, calls = self.assert_held(f, repo=repo, enabled=enabled)
                self.assertFalse(any('/files?' in str(c) for c in calls))
                f['statuses'] = [dict(context='Vercel', state='success')]
                self.assertTrue(self.run_gate(f, repo=repo, enabled=enabled)[1])

    def test_partial_checks_and_statuses_hold(self):
        for field in ['check_count', 'status_count']:
            f = fixture(); f[field] = 101; self.assert_held(f)

    def test_gate_runs_are_excluded_but_unrelated_failure_is_not(self):
        f = fixture(); f['runs'].append(dict(name='merge (#1)', conclusion='failure', check_suite=dict(id=99)))
        self.assertTrue(self.run_gate(f)[1])
        f['runs'].append(dict(name='other', conclusion='failure', check_suite=dict(id=7)))
        self.assert_held(f)

    def test_rerun_requires_successful_current_results_and_retries_lagging_read(self):
        f = fixture(); f['runs'][0]['conclusion'] = 'failure'; self.assert_held(f)
        f['runs'][0]['conclusion'] = 'success'; self.assertTrue(self.run_gate(f)[1])
        f['later_runs'] = copy.deepcopy(f['runs']); f['runs'][0]['conclusion'] = None
        self.assertTrue(self.run_gate(f, trigger='1')[1])
        f['later_runs'][0]['conclusion'] = 'failure'; self.assert_held(f, trigger='1')

    def test_merge_refusal_after_head_moves_does_not_dispatch(self):
        f = fixture(); f['refuse_merge'] = True
        r, attempted, calls = self.run_gate(f)
        self.assertTrue(attempted)
        self.assertIn('moved', r.stdout)
        self.assertFalse(any('/dispatches' in str(c) for c in calls))

    def test_dependabot_still_needs_a_status(self):
        self.assert_held(fixture(), kind='dependabot')

if __name__ == '__main__': unittest.main()
