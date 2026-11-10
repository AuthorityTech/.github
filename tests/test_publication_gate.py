from concurrent.futures import ThreadPoolExecutor
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
RESOLVE = textwrap.dedent(SOURCE.split('      - name: Find the eligible open PRs whose head is this commit\n', 1)[1].split('        run: |\n', 1)[1].split('\n\n  merge:\n', 1)[0])
HEAD = 'a' * 40
BASE = 'b' * 40
REPO = 'Bramwell-Inc/jaxonparrott.com'
NAMES = ['agentic-verify', 'claims', 'portfolio-retired-pointers']

MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root = pathlib.Path(os.environ['FIXTURE'])
f = json.loads((root / 'fixture.json').read_text())
a = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log: log.write(json.dumps(a) + '\\n')
url = next((v for v in a if v.startswith('repos/')), '')
def output(value): print(json.dumps(value))
# fail: {url substring: {times, message, write}} answers that many matching calls with a gh error.
for key, spec in f.get('fail', {}).items():
    if url and key in url and ('-X' in a) == spec.get('write', False) and spec['times'] > 0:
        spec['times'] -= 1
        (root / 'fixture.json').write_text(json.dumps(f))
        sys.stderr.write('gh: ' + spec.get('message', 'Server Error (HTTP 502)') + '\\n')
        sys.exit(1)
if a[:2] == ['pr', 'merge']:
    assert '--match-head-commit' in a and a[a.index('--match-head-commit')+1] == 'a'*40
    assert '--repo' in a and a[a.index('--repo')+1] == os.environ['REPO']
    assert '--squash' in a
    f['pr']['merged'] = True
    (root / 'fixture.json').write_text(json.dumps(f))
    sys.exit(0)
if a[0] != 'api': sys.exit('unexpected non-API command')
if '-X' in a and a[a.index('-X')+1] == 'PUT':
    if f.get('refuse_merge'):
        f['pr']['head']['sha'] = 'c'*40
        (root / 'fixture.json').write_text(json.dumps(f))
        sys.stderr.write('gh: Head branch was modified. Review and try the merge again. (HTTP 409)\\n')
        sys.exit(1)
    if f.get('merged_elsewhere'):
        f['pr']['merged'] = True
        (root / 'fixture.json').write_text(json.dumps(f))
        sys.stderr.write('gh: Pull Request is not mergeable (HTTP 405)\\n')
        sys.exit(1)
    assert '-f' in a and 'sha=' + 'a'*40 in a
    # TOGETHER evaluations merge the same head at once, and GitHub tells each that its merge
    # succeeded, with the same commit (jaxonparrott.com #202): none is answered until all have asked.
    with (root / 'merges').open('a') as m: m.write('.')
    deadline = time.time() + 5
    while (root / 'merges').stat().st_size < int(os.environ.get('TOGETHER', '1')) and time.time() < deadline: time.sleep(0.01)
    output({'sha': 'd'*40}); sys.exit(0)
if url.endswith('/git/refs'):
    assert a[a.index('-X')+1] == 'POST' and 'ref=refs/editorial-merged/' + 'd'*40 in a and 'sha=' + 'd'*40 in a, a
    # GitHub creates a ref once and refuses every later create of it; claimed: it already exists.
    exists = f.get('claimed', False)
    if not exists:
        try: os.close(os.open(root / 'claimed', os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        except FileExistsError: exists = True
    if exists:
        output({'message': 'Reference already exists', 'status': '422'})
        sys.stderr.write('gh: Reference already exists (HTTP 422)\\n'); sys.exit(1)
    output({'ref': 'refs/editorial-merged/' + 'd'*40, 'object': {'sha': 'd'*40, 'type': 'commit'}}); sys.exit(0)
if url.endswith('/dispatches'): sys.exit(0)
if '/rules/branches/' in url:
    expr = a[a.index('--jq') + 1] if '--jq' in a else ''
    for rule in f.get('rules', []):
        if 'required_status_checks' in expr:
            if rule['type'] == 'required_status_checks':
                for check in rule['parameters']['required_status_checks']: print(check['context'])
        elif rule['type'] == 'merge_queue': print('merge_queue')
elif '/files?' in url:
    if f.get('file_api_error'): sys.exit(1)
    output(f['files'])
elif url.endswith('/labels/dependabot-eligible'): output({'name': 'dependabot-eligible'})
elif '/issues/' in url and url.endswith('/labels'): output([{'name': 'dependabot-eligible'}])
elif '/issues/comments/' in url and '-X' in a and a[a.index('-X')+1] == 'PATCH':
    body = next(x.split('=', 1)[1] for x in a if x.startswith('body='))
    cid = url.rsplit('/', 1)[-1]
    for c in f.setdefault('comments', []):
        if str(c.get('id')) == cid: c['body'] = body
    (root / 'fixture.json').write_text(json.dumps(f))
    output({'id': int(cid) if cid.isdigit() else 1})
elif '/issues/' in url and '/comments' in url:
    if '-f' in a and ('-X' not in a or a[a.index('-X')+1] == 'POST'):
        body = next(x.split('=', 1)[1] for x in a if x.startswith('body='))
        comments = f.setdefault('comments', [])
        cid = max([c.get('id', 0) for c in comments] or [0]) + 1
        comments.append({'id': cid, 'body': body, 'user': {'login': 'github-actions[bot]'}})
        (root / 'fixture.json').write_text(json.dumps(f))
        output({'id': cid})
    elif '--jq' in a:
        pass
    else:
        output(f.get('comments') or [])
elif '/pulls/' in url:
    count_file = pathlib.Path(os.environ.get('RUNNER_TEMP', root)) / 'reads'  # this evaluation's own reads
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
    read_file = pathlib.Path(os.environ.get('RUNNER_TEMP', root)) / 'check_reads'
    count = int(read_file.read_text()) if read_file.exists() else 0
    read_file.write_text(str(count+1))
    runs = f.get('later_runs', f['runs']) if count else f['runs']
    output({'total_count': f.get('check_count', len(runs)), 'check_runs': runs})
elif '/status?' in url:
    assert '/commits/' + 'a'*40 + '/' in url
    output({'total_count': f.get('status_count', len(f['statuses'])), 'statuses': f['statuses']})
elif '/commits/' in url and url.endswith('/pulls'): output(f.get('commit_pulls', []))
elif '/commits/' in url: output({'parents': [{'sha': 'b'*40}]})
elif url.count('/') == 2: print('main')
else: sys.exit('unexpected endpoint: ' + url)
'''

def fixture():
    return dict(
        pr=dict(state='open', merged=False, draft=False, title='Fixture', head=dict(sha=HEAD, ref='editorial/post', repo=dict(full_name=REPO)), base=dict(sha=BASE, ref='main'), changed_files=1),
        files=[dict(filename='content/posts/2026-10-31-essay.md', status='modified')],
        statuses=[],
        runs=[dict(name=name, conclusion='success', check_suite=dict(id=1)) for name in [*NAMES, 'page-check']],
    )

class PublicationGate(unittest.TestCase):
    def run_gate(self, f=None, repo=REPO, enabled='true', kind='editorial', required=None, trigger='', together=1):
        """together > 1 runs that many evaluations of the same head at once and returns all their results."""
        f = f or fixture()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'fixture.json').write_text(json.dumps(f))
            for name, script in [('gh', MOCK), ('sleep', '#!/bin/sh\nexit 0\n')]:
                p = root / name; p.write_text(script); p.chmod(0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'], FIXTURE=d, GH_TOKEN='local-fixture-only', REPO=repo, PR='1', SHA=HEAD, KIND=kind, REQUIRED='\n'.join(['Vercel', *NAMES] if required is None else required), CONTENT_WITHOUT_PREVIEW=enabled, WORKFLOW_REF=repo + '/.github/workflows/dependabot-automerge.yml@main', WAIT_MINUTES='0', TRIGGER_SUITE=trigger, BASE_REF='main', TOGETHER=str(together))
            def evaluate(i):
                temp = root / f'runner-{i}'; temp.mkdir()  # each job has its own RUNNER_TEMP
                return subprocess.run(['bash', '-c', SCRIPT], env=dict(env, RUNNER_TEMP=str(temp)), text=True, capture_output=True, timeout=10)
            with ThreadPoolExecutor(together) as pool: results = list(pool.map(evaluate, range(together)))
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()] if (root / 'calls.jsonl').exists() else []
            merged = any(('-X' in c and 'PUT' in c) or c[:2] == ['pr', 'merge'] for c in calls)
            return (results[0] if together == 1 else results), merged, calls

    def run_rounds(self, f=None, rounds=2, repo=REPO, enabled='true', kind='editorial', required=None, trigger=''):
        f = f or fixture()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'fixture.json').write_text(json.dumps(f))
            for name, script in [('gh', MOCK), ('sleep', '#!/bin/sh\nexit 0\n')]:
                p = root / name; p.write_text(script); p.chmod(0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'], FIXTURE=d, RUNNER_TEMP=d, GH_TOKEN='local-fixture-only', REPO=repo, PR='1', SHA=HEAD, KIND=kind, REQUIRED='\n'.join(['Vercel', *NAMES] if required is None else required), CONTENT_WITHOUT_PREVIEW=enabled, WORKFLOW_REF=repo + '/.github/workflows/dependabot-automerge.yml@main', WAIT_MINUTES='0', TRIGGER_SUITE=trigger, BASE_REF='main')
            out = []
            for _ in range(rounds):
                log = root / 'calls.jsonl'
                if log.exists(): log.unlink()
                result = subprocess.run(['bash', '-c', SCRIPT], env=env, text=True, capture_output=True, timeout=10)
                calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
                merged = any(('-X' in c and 'PUT' in c) or c[:2] == ['pr', 'merge'] for c in calls)
                out.append((result, merged, calls))
            return out, json.loads((root / 'fixture.json').read_text())

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

    def test_moved_closed_draft_fork_wrong_base_own_merger_queue_hold(self):
        for field, value in [('draft', True), ('state', 'closed'), ('head.sha', 'c'*40), ('head.ref', 'land/hub-evidence/r1'), ('base.ref', 'other'), ('head.repo.full_name', 'fork/site'), ('user', dict(login='dependabot[bot]'))]:
            f = fixture(); obj = f['pr']; parts = field.split('.')
            for part in parts[:-1]: obj = obj[part]
            obj[parts[-1]] = value
            self.assert_held(f)
        f = fixture(); f['rules'] = [dict(type='merge_queue')]; self.assert_held(f)

    # The branch name is not a hold (Jaxon, 2026-11-10): any agent branch in the
    # repository merges on the same checks an editorial/ branch does.
    def test_any_branch_name_merges_on_the_same_checks(self):
        for ref in ['writer/brief-2026-11-09', 'claude/fix-nav', 'editor/retire-page', 'fix/platform']:
            with self.subTest(ref=ref):
                f = fixture(); f['pr']['head']['ref'] = ref
                result, merged, calls = self.run_gate(f)
                self.assertTrue(merged, result.stdout + result.stderr)
                self.assertEqual(sum('/dispatches' in str(c) for c in calls), 1)
                f = fixture(); f['pr']['head']['ref'] = ref; f['runs'][0]['conclusion'] = 'failure'
                self.assert_held(f)

    def test_recovery_events_find_any_branch_but_never_dependabot_or_land(self):
        def pull(n, ref, login='writer-seat'):
            return dict(number=n, state='open', draft=False, user=dict(login=login), head=dict(sha=HEAD, ref=ref, repo=dict(full_name=REPO)), base=dict(ref='main'))
        f = fixture(); f['commit_pulls'] = [pull(1, 'writer/brief'), pull(2, 'dependabot/npm/next-15.1', 'dependabot[bot]'), pull(3, 'land/hub-evidence/r1'), pull(4, 'editorial/post'), dict(pull(5, 'claude/draft'), draft=True)]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'fixture.json').write_text(json.dumps(f))
            for name, script in [('gh', MOCK), ('sleep', '#!/bin/sh\nexit 0\n')]:
                p = root / name; p.write_text(script); p.chmod(0o755)
            out = root / 'output'
            env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'], FIXTURE=d, GH_TOKEN='local-fixture-only', REPO=REPO, EVENT='status', PR='', PR_SHA='', PR_AUTHOR='', SHA=HEAD, GITHUB_OUTPUT=str(out))
            r = subprocess.run(['bash', '-c', RESOLVE], env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(out.read_text().split('prs=', 1)[1]), [dict(number=1, sha=HEAD, kind='editorial'), dict(number=4, sha=HEAD, kind='editorial')])

    def test_the_job_filter_takes_any_branch_only_for_a_caller_that_lists_its_checks(self):
        cond = SOURCE.split('  resolve:\n', 1)[1].split('    runs-on:', 1)[0]
        self.assertNotIn("'editorial/'", cond)
        self.assertEqual(cond.count("inputs.editorial-required-checks != ''"), 3)
        self.assertEqual(cond.count("startsWith(github.event.pull_request.head.ref, 'land/')") + cond.count("startsWith(github.event.workflow_run.head_branch, 'land/')"), 2)

    def test_other_callers_and_default_input_retain_status_dependency(self):
        self.assert_held(fixture(), enabled='false')
        for repo in ['AuthorityTech/website', 'AuthorityTech/christianlehman.com', 'Bramwell-Inc/machinerelations.ai', 'Bramwell-Inc/paralabs.ai', 'Bramwell-Inc/paralax.ai', 'AuthorityTech/app']:
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
        self.assertFalse(any('/dispatches' in str(c) or '/git/refs' in str(c) for c in calls))

    # 2026-11-06: two evaluations of jaxonparrott.com #202's green head merged it at once. GitHub told
    # both their merge succeeded, with the same commit, and both sent editorial-merged, so two Search
    # Indexing runs submitted the same pages. Only the evaluation that creates the claim ref sends.
    def test_concurrent_evaluations_both_told_merged_send_one_dispatch(self):
        results, merged, calls = self.run_gate(together=2)
        for r in results: self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sum('-X' in c and 'PUT' in c for c in calls), 2, 'both merges succeeded')
        self.assertEqual(sum('/dispatches' in str(c) for c in calls), 1)
        self.assertEqual(sorted('a concurrent evaluation claimed' in r.stdout for r in results), [False, True])

    def test_a_claim_that_cannot_be_settled_still_sends_one_dispatch_with_a_warning(self):
        server = 'Server Error (HTTP 502)'
        denied = 'Resource not accessible by integration (HTTP 403)'
        # (claim failures before an answer, its message, ref already there, dispatches, warned)
        for fails, message, claimed, sent, warned in [(0, server, False, 1, False), (1, server, False, 1, False), (99, server, False, 1, True),
                                                       (99, denied, False, 1, True), (1, server, True, 1, True), (0, server, True, 0, False)]:
            f = fixture(); f['claimed'] = claimed; f['fail'] = {'/git/refs': dict(times=fails, message=message, write=True)}
            with self.subTest(fails=fails, message=message, claimed=claimed):
                r, merged, calls = self.run_gate(f)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertTrue(merged)
                self.assertEqual(sum('/dispatches' in str(c) for c in calls), sent, r.stdout)
                self.assertEqual('could not be claimed' in r.stdout, warned, r.stdout)
                self.assertLessEqual(sum('/git/refs' in str(c) for c in calls), 3)

    def test_a_merge_refused_because_another_evaluation_merged_sends_nothing(self):
        f = fixture(); f['merged_elsewhere'] = True
        r, attempted, calls = self.run_gate(f)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('merged by a concurrent evaluation', r.stdout)
        self.assertFalse(any('/dispatches' in str(c) or '/git/refs' in str(c) for c in calls))

    def test_a_dispatch_that_fails_after_the_claim_still_fails_the_evaluation(self):
        f = fixture(); f['fail'] = {'/dispatches': dict(times=99)}
        r, merged, calls = self.run_gate(f)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('editorial-merged was not sent', r.stdout)
        self.assertEqual(sum('/dispatches' in str(c) for c in calls), 3)

    # 2026-11-05: jaxonparrott.com main is to require jp-overlap, a status the Gateway posts within ten
    # minutes. A Dependabot head is green sooner: the gate tried one merge, GitHub refused it, and no
    # later event would evaluate that PR again. A context the base's rules require is required here.
    def test_a_status_the_base_rules_require_holds_either_kind_until_posted(self):
        rules = [dict(type='required_status_checks', parameters=dict(required_status_checks=[dict(context='jp-overlap')])), dict(type='deletion', parameters=None)]
        for kind, required in [('editorial', None), ('dependabot', [])]:
            f = fixture(); f['rules'] = rules; f['statuses'] = [dict(context='Vercel', state='success')]
            with self.subTest(kind=kind, posted=False):
                r, calls = self.assert_held(f, kind=kind, required=required)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn('jp-overlap', r.stdout)
            for state in ['pending', 'failure', 'error']:
                f = fixture(); f['rules'] = rules; f['statuses'] = [dict(context='Vercel', state='success'), dict(context='jp-overlap', state=state)]
                with self.subTest(kind=kind, state=state): self.assert_held(f, kind=kind, required=required)
            f = fixture(); f['rules'] = rules; f['statuses'] = [dict(context='Vercel', state='success'), dict(context='jp-overlap', state='success')]
            with self.subTest(kind=kind, posted=True):
                r, merged, calls = self.run_gate(f, kind=kind, required=required)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertTrue(merged, r.stdout)
        # A base whose rules require nothing changes nothing.
        f = fixture(); f['rules'] = [dict(type='deletion', parameters=None)]
        self.assertTrue(self.run_gate(f)[1])
        # The rules are read with every read of the checks, so a waiting evaluation sees a rule added meanwhile.
        f = fixture(); f['rules'] = rules; f['statuses'] = [dict(context='Vercel', state='success')]
        r, merged, calls = self.run_gate(f, kind='dependabot', required=[])
        reads = [c for c in calls if any('/rules/branches/' in str(x) for x in c) and 'required_status_checks' in ' '.join(c)]
        checks = [c for c in calls if any('/check-runs?' in str(x) for x in c)]
        self.assertEqual(len(reads), len(checks), r.stdout)

    def test_dependabot_still_needs_a_status(self):
        self.assert_held(fixture(), kind='dependabot')

    def test_app_dependabot_preserves_checks_exact_head_and_queue_handling(self):
        def app_fixture():
            f = fixture()
            f['pr']['head'].update(ref='dependabot/npm_and_yarn/patch-updates', repo=dict(full_name='AuthorityTech/app'))
            f['runs'] = [dict(name=name, conclusion='success', check_suite=dict(id=1)) for name in ['delivery-gate', 'live-database-tests', 'lat-check']]
            f['statuses'] = [dict(context='Railway', state='success')]
            return f

        for enabled in ['false', 'true']:
            args = dict(repo='AuthorityTech/app', enabled=enabled, kind='dependabot', required=[])
            r, merged, calls = self.run_gate(app_fixture(), **args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(merged)
            self.assertFalse(any('/files?' in str(c) or '/dispatches' in str(c) or '/git/refs' in str(c) for c in calls))
            for state in ['absent', 'pending', 'failure', 'error']:
                f = app_fixture(); f['statuses'] = [] if state == 'absent' else [dict(context='Railway', state=state)]
                self.assert_held(f, **args)
            for state in [None, 'failure', 'cancelled', 'timed_out']:
                f = app_fixture(); f['runs'][0]['conclusion'] = state
                self.assert_held(f, **args)
            for field, value in [('draft', True), ('head', dict(sha='c'*40, ref='dependabot/npm_and_yarn/patch-updates', repo=dict(full_name='AuthorityTech/app')))]:
                f = app_fixture(); f['pr'][field] = value
                self.assert_held(f, **args)
            f = app_fixture(); f['rules'] = [dict(type='merge_queue')]
            r, merged, calls = self.run_gate(f, **args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(merged)
            self.assertTrue(any('labels[]=dependabot-eligible' in c for c in calls))
            self.assertTrue(any('/issues/1/comments' in str(c) and '-f' in c for c in calls))
            self.assertFalse(any('/files?' in str(c) or '/dispatches' in str(c) for c in calls))

    # 2026-11-04: one HTTP 502 on a read ended the only evaluation website #939's green head got, and
    # it sat unmerged until a seat re-ran the job. A transient server error must not end an evaluation.
    def test_a_server_error_on_any_read_is_retried_and_the_head_still_merges(self):
        baseline = self.run_gate()[2]
        messages = ['Server Error (HTTP 502)', 'HTTP 503: Service Unavailable',
                    'Get "https://api.github.com/x": read tcp 10.0.0.1:443: connection reset by peer']
        for key in ['/rules/branches/', '/pulls/', '/files?', '/status?', '/actions/runs?', '/check-runs?']:
            for message in messages:
                f = fixture(); f['fail'] = {key: dict(times=1, message=message)}
                with self.subTest(key=key, message=message):
                    r, merged, calls = self.run_gate(f)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertTrue(merged, r.stdout + r.stderr)
                    self.assertEqual(sum(key in str(c) for c in calls), sum(key in str(c) for c in baseline) + 1)
                    self.assertEqual(sum('/dispatches' in str(c) for c in calls), 1)
                    self.assertEqual(sum('-X' in c and 'PUT' in c for c in calls), 1)

    def test_a_server_error_that_persists_fails_the_evaluation_visibly_and_never_merges(self):
        f = fixture(); f['fail'] = {'/status?': dict(times=99)}
        r, merged, calls = self.run_gate(f)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(merged)
        self.assertIn('HTTP 502', r.stderr)
        self.assertEqual(sum('/status?' in str(c) for c in calls), 4, 'four tries, then the error stands')

    def test_client_errors_and_writes_are_never_retried_by_the_read_wrapper(self):
        f = fixture(); f['fail'] = {'/status?': dict(times=99, message='Not Found (HTTP 404)')}
        r, merged, calls = self.run_gate(f)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(merged)
        self.assertEqual(sum('/status?' in str(c) for c in calls), 1)
        f = fixture(); f['fail'] = {'/merge': dict(times=1, write=True)}
        r, merged, calls = self.run_gate(f)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sum('-X' in c and 'PUT' in c for c in calls), 2, "the merge loop's own second try, nothing more")
        self.assertEqual(sum('/dispatches' in str(c) for c in calls), 1)

    def test_a_recovery_event_survives_a_server_error_while_finding_its_pr(self):
        pull = dict(number=1, state='open', draft=False, head=dict(sha=HEAD, ref='editorial/post', repo=dict(full_name=REPO)), base=dict(ref='main'))
        for times in [0, 1]:
            f = fixture(); f['commit_pulls'] = [pull]; f['fail'] = {'/commits/' + HEAD + '/pulls': dict(times=times)}
            with tempfile.TemporaryDirectory() as d, self.subTest(times=times):
                root = Path(d)
                (root / 'fixture.json').write_text(json.dumps(f))
                for name, script in [('gh', MOCK), ('sleep', '#!/bin/sh\nexit 0\n')]:
                    p = root / name; p.write_text(script); p.chmod(0o755)
                out = root / 'output'
                env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'], FIXTURE=d, GH_TOKEN='local-fixture-only', REPO=REPO, EVENT='workflow_run', PR='', PR_SHA='', PR_AUTHOR='', SHA=HEAD, GITHUB_OUTPUT=str(out))
                r = subprocess.run(['bash', '-c', RESOLVE], env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(json.loads(out.read_text().split('prs=', 1)[1]), [dict(number=1, sha=HEAD, kind='editorial')])

    # Listing Vercel does not make a missing or skipped page-check a hold. It notes
    # that the run did not judge, and merges. A finished failure still stops the merge.
    # A caller that does not list Vercel, and Dependabot, get neither note nor hold.
    def test_unavailable_page_check_is_noted_and_a_finished_failure_still_holds(self):
        def drop_page(f):
            f['runs'] = [c for c in f['runs'] if c['name'] != 'page-check']
            return f

        def set_page(f, state):
            next(c for c in f['runs'] if c['name'] == 'page-check')['conclusion'] = state
            return f

        def benign(f):
            f['runs'].extend([
                dict(name='indexnow', conclusion='skipped', check_suite=dict(id=4)),
                dict(name='site-integrity / internal-links', conclusion='skipped', check_suite=dict(id=5)),
                dict(name='coverage', conclusion='neutral', check_suite=dict(id=6)),
            ])
            return f

        def note_writes(calls):
            writes = []
            for c in calls:
                if not any(x.startswith('body=') and 'automerge-page-check-unjudged' in x for x in c):
                    continue
                if '-X' in c and c[c.index('-X')+1] == 'PATCH':
                    writes.append('PATCH')
                else:
                    writes.append('POST')
            return writes

        def ready(repo, required):
            f = benign(fixture())
            f['pr']['head']['repo']['full_name'] = repo
            if repo == REPO:
                f['statuses'] = [dict(context='jp-overlap', state='success')]
            else:
                f['statuses'] = [dict(context='Vercel', state='success')]
            return f

        callers = [
            ('AuthorityTech/website', ['Vercel', 'portfolio-retired-pointers']),
            ('AuthorityTech/christianlehman.com', ['Vercel', 'portfolio-retired-pointers']),
            ('Bramwell-Inc/machinerelations.ai', ['Vercel', 'portfolio-retired-pointers']),
            ('Bramwell-Inc/paralabs.ai', ['Vercel', 'agentic-verify', 'portfolio-retired-pointers']),
            ('Bramwell-Inc/paralax.ai', ['Vercel', 'agentic-verify', 'portfolio-retired-pointers']),
            ('Bramwell-Inc/presspool.ai', ['Vercel']),
            ('Bramwell-Inc/jaxonparrott.com', ['Vercel', 'agentic-verify', 'portfolio-retired-pointers', 'claims', 'jp-overlap']),
        ]
        for repo, required in callers:
            result, merged, calls = self.run_gate(ready(repo, required), repo=repo, required=required)
            with self.subTest(repo=repo, judged=True):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(merged, result.stdout)
                self.assertEqual(note_writes(calls), [])
                self.assertNotIn('::warning::Page check did not judge', result.stdout)
            f = drop_page(ready(repo, required))
            result, merged, calls = self.run_gate(f, repo=repo, required=required)
            with self.subTest(repo=repo, judged=False):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(merged, result.stdout)
                self.assertEqual(note_writes(calls), [])
                self.assertIn('::warning::Page check did not judge', result.stdout)
                self.assertNotIn('when this merged', result.stdout)
        # Vercel first or later is the same signal. A missing run is noted, not held.
        for required in (['Vercel', 'portfolio-retired-pointers'], ['portfolio-retired-pointers', 'Vercel']):
            f = drop_page(ready('Bramwell-Inc/presspool.ai', required))
            result, merged, calls = self.run_gate(f, repo='Bramwell-Inc/presspool.ai', required=required)
            with self.subTest(required=required):
                self.assertTrue(merged, result.stdout + result.stderr)
                self.assertEqual(note_writes(calls), [])
                self.assertIn('::warning::Page check did not judge', result.stdout)
        # Skipped and neutral are terminal and unjudged: merge, with a note.
        # Still running waits. A finished failure, the blocking case, does not merge.
        for state in ('skipped', 'neutral'):
            result, merged, calls = self.run_gate(benign(set_page(fixture(), state)))
            with self.subTest(state=state):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(merged, result.stdout)
                self.assertEqual(note_writes(calls), [])
                self.assertIn('::warning::Page check did not judge', result.stdout)
        result, merged, calls = self.run_gate(benign(set_page(fixture(), None)))
        self.assertFalse(merged, result.stdout)
        self.assertIn('waiting', result.stdout)
        self.assertEqual(note_writes(calls), [])
        result, merged, calls = self.run_gate(benign(set_page(fixture(), 'failure')))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(merged, result.stdout)
        self.assertIn('a check failed', result.stdout)
        self.assertEqual(note_writes(calls), [])
        # Success, including the job's own not-judged success, merges with no note.
        # A path-filter skip beside it is not that note.
        result, merged, calls = self.run_gate(benign(fixture()))
        self.assertTrue(merged, result.stdout)
        self.assertEqual(note_writes(calls), [])
        self.assertIn('content_only=true', result.stdout)
        # The workflow name is not the check run. Absent page-check is noted, not held.
        f = drop_page(fixture())
        f['runs'].append(dict(name='Page check', conclusion='success', check_suite=dict(id=3)))
        result, merged, calls = self.run_gate(f)
        self.assertTrue(merged, result.stdout + result.stderr)
        self.assertEqual(note_writes(calls), [])
        self.assertIn('::warning::Page check did not judge', result.stdout)
        # Writing page-check into the required list is still a presence hold.
        # This gate does not write it there.
        f = drop_page(ready('Bramwell-Inc/presspool.ai', ['Vercel', 'page-check']))
        result, calls = self.assert_held(f, repo='Bramwell-Inc/presspool.ai', required=['Vercel', 'page-check'])
        self.assertIn('page-check', result.stdout)
        self.assertEqual(note_writes(calls), [])
        # An existing required check still holds when page-check succeeded.
        f = benign(fixture())
        next(c for c in f['runs'] if c['name'] == 'portfolio-retired-pointers')['conclusion'] = 'skipped'
        result, calls = self.assert_held(f)
        self.assertEqual(note_writes(calls), [])
        # A code change still needs the preview.
        f = benign(fixture())
        f['files'].append(dict(filename='src/proxy.ts', status='modified'))
        f['pr']['changed_files'] = 2
        self.assert_held(f)
        f['statuses'] = [dict(context='Vercel', state='success')]
        result, merged, calls = self.run_gate(f)
        self.assertTrue(merged, result.stdout)
        self.assertEqual(note_writes(calls), [])
        # The comments API is not on this path, so a comments failure does not matter.
        f = drop_page(fixture())
        f['fail'] = {'/comments': dict(times=4, message='Server Error (HTTP 502)')}
        result, merged, calls = self.run_gate(f)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(merged, result.stdout + result.stderr)
        self.assertIn('::warning::Page check did not judge', result.stdout)
        self.assertNotIn('could not be read', result.stdout)
        self.assertEqual(note_writes(calls), [])
        # No Vercel in the required list, and Dependabot, are not noted or held.
        f = fixture()
        f['pr']['head']['repo']['full_name'] = 'AuthorityTech/app'
        f['statuses'] = [dict(context='Vercel', state='success')]
        f['runs'] = [
            dict(name='delivery-gate', conclusion='success', check_suite=dict(id=1)),
            dict(name='page-check', conclusion='skipped', check_suite=dict(id=3)),
            dict(name='indexnow', conclusion='skipped', check_suite=dict(id=4)),
            dict(name='Vercel Preview Comments', conclusion='success', check_suite=dict(id=8)),
        ]
        for kind, required in [
            ('editorial', ['delivery-gate']),
            ('editorial', ['Vercel Preview Comments']),
            ('editorial', []),
            ('dependabot', ['Vercel', *NAMES]),
        ]:
            result, merged, calls = self.run_gate(f, repo='AuthorityTech/app', kind=kind, required=required)
            with self.subTest(kind=kind, required=required):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(merged, result.stdout + result.stderr)
                self.assertEqual(note_writes(calls), [])
        result, merged, calls = self.run_gate(f, repo='AuthorityTech/app', required=['vercel'])
        before = result.stdout.split('checks=', 1)[0]
        self.assertFalse(merged, result.stdout)
        self.assertIn('required: ["vercel"]', before)
        self.assertNotIn('page-check', before)
        self.assertEqual(note_writes(calls), [])

    def test_overlapping_unjudged_evaluations_only_record_their_own_run(self):
        self.assertNotIn('automerge-page-check-unjudged', SOURCE)
        self.assertNotIn('when this merged', SOURCE)
        f = fixture()
        f['runs'] = [c for c in f['runs'] if c['name'] != 'page-check']
        f['refuse_merge'] = True
        dirs = []
        procs = []
        try:
            for _ in range(2):
                d = tempfile.TemporaryDirectory()
                dirs.append(d)
                root = Path(d.name)
                (root / 'fixture.json').write_text(json.dumps(f))
                for name, script in [('gh', MOCK), ('sleep', '#!/bin/sh\nexit 0\n')]:
                    p = root / name
                    p.write_text(script)
                    p.chmod(0o755)
                env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], FIXTURE=str(root), RUNNER_TEMP=str(root), GH_TOKEN='local-fixture-only', REPO=REPO, PR='1', SHA=HEAD, KIND='editorial', REQUIRED='\n'.join(['Vercel', *NAMES]), CONTENT_WITHOUT_PREVIEW='true', WORKFLOW_REF=REPO + '/.github/workflows/dependabot-automerge.yml@main', WAIT_MINUTES='0', TRIGGER_SUITE='', BASE_REF='main', GITHUB_STEP_SUMMARY=str(root / 'summary.md'))
                procs.append(subprocess.Popen(['bash', '-c', SCRIPT], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            for proc, d in zip(procs, dirs):
                stdout, stderr = proc.communicate(timeout=15)
                self.assertEqual(proc.returncode, 0, stdout + stderr)
                self.assertIn('::warning::Page check did not judge', stdout)
                self.assertIn('not a hold', stdout)
                self.assertNotIn('when this merged', stdout)
                self.assertIn('moved', stdout)
                calls = [json.loads(line) for line in (Path(d.name) / 'calls.jsonl').read_text().splitlines()]
                self.assertFalse(any('comments' in x and ('-f' in c or ('-X' in c and c[c.index('-X')+1] == 'PATCH')) for c in calls for x in c))
                self.assertFalse(any('/dispatches' in str(c) for c in calls))
                summary = (Path(d.name) / 'summary.md').read_text()
                self.assertIn(f'Page check did not judge {HEAD}', summary)
                self.assertNotIn('when this merged', summary)
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
            for d in dirs:
                d.cleanup()

    def test_resolve_and_merge_carry_the_same_read_retry(self):
        def wrapper(script):
            start = script.index('gh() {')
            return script[start:script.index('\n}\n', start) + 3]
        self.assertEqual(wrapper(RESOLVE), wrapper(SCRIPT))

if __name__ == '__main__': unittest.main()
