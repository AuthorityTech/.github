"""land-checked moves main only to the exact commit whose Vercel status passed.

Runs land.sh against a local bare origin, with gh and sleep stubbed on PATH; no network.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github/actions/land-checked/land.sh'
REPO = 'AuthorityTech/website'
BRANCH = 'land/producer/7-1'

# Answers the statuses read as GitHub does (newest first) and applies --jq with jq, as gh does.
# Records what origin held at each read, and moves origin's main to move_to at the first read.
GH = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
root = pathlib.Path(os.environ['FIXTURE'])
f = json.loads((root / 'fixture.json').read_text())
a = [x for x in sys.argv[1:] if x != '--paginate']
if '-X' in a:
    path = a[a.index('-X') + 2]
    assert a[a.index('-X') + 1] == 'POST' and '/statuses/' in path, a
    fields = dict(a[i + 1].split('=', 1) for i, x in enumerate(a) if x == '-f')
    f.setdefault('posted', []).append([fields['context'], path.rsplit('/', 1)[1]])
    f['extra_statuses'].append({'context': fields['context'], 'state': fields['state']})
    (root / 'fixture.json').write_text(json.dumps(f))
    sys.exit(0)
if '/actions/runs/' in a[1] and a[1].split('?')[0].endswith('/jobs'):
    jobs = [{'name': c['name'], 'conclusion': c.get('conclusion'), 'html_url': 'https://jobs.example/' + c['name']} for c in f.get('jobs', f.get('checks', []))]
    out = subprocess.run(['jq', '-r', a[-1]], input=json.dumps({'jobs': jobs}), capture_output=True, text=True, check=True)
    sys.stdout.write(out.stdout)
    sys.exit(0)
if a[0] == 'workflow':
    f.setdefault('dispatches', []).append(a)
    (root / 'fixture.json').write_text(json.dumps(f))
    sys.exit(0)
if '/rules/branches/' in a[1]:
    print('\n'.join(json.dumps({'context': r} if isinstance(r, str) else r) for r in f.get('required', ['Vercel'])))
    sys.exit(0)
if '/actions/workflows/' in a[1]:
    sha = a[1].split('head_sha=')[1].split('&')[0]
    runs = [{'id': 10, 'head_sha': sha, 'conclusion': 'success'}]
    if a[-1] != '.workflow_runs | map(.id) | max // 0' and f.get('dispatch_state') != 'missing':
        runs.append({'id': 11, 'head_sha': sha, 'conclusion': None if f.get('dispatch_state') == 'pending' else f.get('dispatch_state', 'success')})
    out = subprocess.run(['jq', '-r', a[-1]], input=json.dumps({'workflow_runs': runs}), capture_output=True, text=True, check=True)
    sys.stdout.write(out.stdout)
    sys.exit(0)
if '/check-runs?' in a[1]:
    checks = f.get('checks', [])
    print(json.dumps({'total_count': len(checks), 'check_runs': checks}))
    sys.exit(0)
assert a[0] == 'api' and '/status?' in a[1], a
sha = a[1].split('/commits/')[1].split('/')[0]
def ref(name):
    out = subprocess.run(['git', '--git-dir', os.environ['ORIGIN'], 'rev-parse', '-q', '--verify', name], capture_output=True, text=True).stdout.strip()
    return out or None
if sha not in f['seen']:
    f['seen'].append(sha)
build = f['seen'].index(sha)
retry_ref = ref('refs/heads/' + os.environ['BRANCH'] + '-retry')
n = sum(1 for r in f['reads'] if r['sha'] == sha)
f['reads'].append({'sha': sha, 'main': ref('refs/heads/main'), 'land': ref('refs/heads/' + os.environ['BRANCH']), 'retry': retry_ref})
if f['move_to'] and len(f['reads']) == 1:
    subprocess.run(['git', '--git-dir', os.environ['ORIGIN'], 'update-ref', 'refs/heads/main', f['move_to']], check=True)
(root / 'fixture.json').write_text(json.dumps(f))
if f.get('drop_first_push'):
    if retry_ref != sha:
        states = ['none']
    else:
        m = sum(1 for r in f['reads'] if r['sha'] == sha and r.get('retry') == sha) - 1
        states = f['builds'][min(build, len(f['builds']) - 1)][:m + 1]
else:
    states = f['builds'][min(build, len(f['builds']) - 1)][:n + 1]
statuses = [{'context': 'Vercel', 'state': s, 'target_url': 'https://vercel.example/' + s} for s in reversed(states) if s != 'none']
statuses = list({s['context']: s for s in reversed(statuses)}.values()) + f.get('extra_statuses', [])
out = json.dumps({'total_count': len(statuses), 'statuses': statuses})
if '--jq' in a:
    out = subprocess.run(['jq', '-r', a[a.index('--jq') + 1]], input=out, capture_output=True, text=True, check=True).stdout
sys.stdout.write(out)
'''


class LandChecked(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert shutil.which('jq'), 'the gh stub applies --jq with jq, as gh does'

    def land(self, builds, other=None, rounds=6, minutes=15, after_rebase='', required=None, checks=None, workflows='', dispatch_state='success', extra_statuses=None, jobs=None, drop_first_push=False, fake_clock=False):
        """Lands one producer commit; builds lists Vercel's states per distinct commit, read by read.
        other ('add' or 'conflict') is another writer's commit, which becomes main at the first read."""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        env = dict(os.environ, HOME=str(d), GIT_CONFIG_NOSYSTEM='1', GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.com', GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.com')
        origin, work, writer = d / 'origin.git', d / 'work', d / 'writer'

        def git(cwd, *args):
            return subprocess.run(['git', *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()

        git(d, 'init', '-q', '--bare', '-b', 'main', str(origin))
        git(d, 'clone', '-q', str(origin), str(work))
        (work / 'data.txt').write_text('base\n')
        git(work, 'add', '-A')
        git(work, 'commit', '-qm', 'base')
        git(work, 'push', '-q', 'origin', 'HEAD:main')
        base = git(work, 'rev-parse', 'HEAD')
        move_to = None
        if other:
            git(d, 'clone', '-q', str(origin), str(writer))
            (writer / ('data.txt' if other == 'conflict' else 'other.txt')).write_text('theirs\n')
            git(writer, 'add', '-A')
            git(writer, 'commit', '-qm', 'another writer')
            git(writer, 'push', '-q', 'origin', 'HEAD:refs/heads/other')
            move_to = git(writer, 'rev-parse', 'HEAD')
        (work / 'data.txt').write_text('ours\n')
        git(work, 'commit', '-qam', 'producer data')
        ours = git(work, 'rev-parse', 'HEAD')

        stubs = d / 'bin'
        stubs.mkdir()
        stub_files = [('gh', GH)]
        if fake_clock:
            (d / 'clock').write_text('1000\n')
            stub_files.extend([
                ('sleep', '#!/bin/sh\nval=$(cat "$FIXTURE/clock")\necho $((val + ${1:-0})) > "$FIXTURE/clock"\n'),
                ('date', '#!/bin/sh\nif [ "$1" = "+%s" ]; then cat "$FIXTURE/clock"; else exec /bin/date "$@"; fi\n'),
            ])
        else:
            stub_files.append(('sleep', '#!/bin/sh\nexit 0\n'))
        for name, body in stub_files:
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        (d / 'fixture.json').write_text(json.dumps(dict(builds=builds, move_to=move_to, seen=[], reads=[], required=required if required is not None else ['Vercel'], checks=checks or [], dispatch_state=dispatch_state, extra_statuses=extra_statuses or [], drop_first_push=drop_first_push, **({'jobs': jobs} if jobs is not None else {}))))
        output = d / 'output'
        output.write_text('')
        env.update(PATH=str(stubs) + os.pathsep + env['PATH'], FIXTURE=str(d), ORIGIN=str(origin), BRANCH=BRANCH,
                   GITHUB_REPOSITORY=REPO, GITHUB_RUN_ID='7', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_WORKFLOW_REF=REPO + '/.github/workflows/producer.yml@refs/heads/main',
                   GITHUB_OUTPUT=str(output), GH_TOKEN='local-fixture-only',
                   CHECK_WORKFLOWS=workflows, BUILD_MINUTES=str(minutes), ROUNDS=str(rounds), AFTER_REBASE=after_rebase)
        result = subprocess.run(['bash', str(SCRIPT)], cwd=work, env=env, capture_output=True, text=True, timeout=60)
        f = json.loads((d / 'fixture.json').read_text())

        def at(name):
            r = subprocess.run(['git', '--git-dir', str(origin), 'rev-parse', '-q', '--verify', name], env=env, capture_output=True, text=True)
            return r.stdout.strip() or None

        return SimpleNamespace(
            code=result.returncode, log=result.stdout + result.stderr, base=base, ours=ours, other=move_to,
            main=at('refs/heads/main'), land=at('refs/heads/' + BRANCH), retry=at('refs/heads/' + BRANCH + '-retry'), seen=f['seen'], reads=f['reads'], dispatches=f.get('dispatches', []), posted=f.get('posted', []),
            outputs=dict(line.split('=', 1) for line in output.read_text().splitlines()),
            git=lambda *args: git(work, *args), work=work)

    def test_lands_the_exact_built_sha_only_after_success(self):
        r = self.land([['none', 'pending', 'success']])
        self.assertEqual(r.code, 0, r.log)
        self.assertEqual(r.seen, [r.ours])
        self.assertEqual(r.main, r.ours)
        # Until Vercel passed, main stayed put and the land branch held the commit being built.
        self.assertEqual([(x['main'], x['land']) for x in r.reads], [(r.base, r.ours)] * 3)
        self.assertIsNone(r.land)
        self.assertEqual(r.outputs, {'sha': r.ours, 'landed': 'true'})

    def test_pending_until_timeout_leaves_main_untouched(self):
        r = self.land([['pending']], minutes=0)
        self.assertEqual(r.code, 1, r.log)
        self.assertEqual((r.main, r.land, r.outputs), (r.base, r.ours, {}))
        self.assertIn('checks still pending for ' + r.ours + ' after 0 min', r.log)

    def test_failure_or_error_leaves_main_untouched(self):
        for state in ['failure', 'error']:
            with self.subTest(state):
                r = self.land([['pending', state]])
                self.assertEqual(r.code, 1, r.log)
                self.assertEqual((r.main, r.land, r.outputs), (r.base, r.ours, {}))
                self.assertIn('a check failed for ', r.log)

    def test_main_moved_rebases_builds_again_and_lands(self):
        # after-rebase passes only on the rebased tree, which has the other writer's file.
        r = self.land([['success'], ['pending', 'success']], other='add', after_rebase='test -f other.txt')
        self.assertEqual(r.code, 0, r.log)
        self.assertEqual(len(r.seen), 2, r.log)
        first, rebased = r.seen
        self.assertEqual(first, r.ours)
        self.assertEqual(r.main, rebased)
        self.assertEqual(r.git('rev-parse', rebased + '^'), r.other)
        self.assertEqual(r.git('show', rebased + ':data.txt'), 'ours')
        # The rebased commit went to the land branch and was waited for while main stayed the other writer's.
        self.assertEqual([(x['main'], x['land']) for x in r.reads if x['sha'] == rebased], [(r.other, rebased)] * 2)
        self.assertIsNone(r.land)
        self.assertEqual(r.outputs, {'sha': rebased, 'landed': 'true'})

    def test_rebase_conflict_exits_1(self):
        r = self.land([['success']], other='conflict')
        self.assertEqual(r.code, 1, r.log)
        self.assertEqual((r.main, r.land, r.outputs), (r.other, r.ours, {}))
        self.assertIn('git pull --rebase origin main failed', r.log)
        self.assertFalse((r.work / '.git/rebase-merge').exists() or (r.work / '.git/rebase-apply').exists())

    def test_rounds_are_bounded(self):
        r = self.land([['success']], other='add', rounds=1)
        self.assertEqual(r.code, 1, r.log)
        self.assertEqual((r.main, r.land, r.outputs), (r.other, r.ours, {}))
        self.assertIn('main moved during each of 1 builds', r.log)

    def test_failed_after_rebase_stops_before_another_build(self):
        r = self.land([['success']], other='add', after_rebase='test ! -f other.txt')
        self.assertEqual(r.code, 1, r.log)
        self.assertEqual((r.main, r.land, r.seen), (r.other, r.ours, [r.ours]))
        self.assertIn('after-rebase failed', r.log)

    def test_app_check_run_without_status_lands(self):
        r = self.land([['none']], required=['source'], checks=[{'name': 'source', 'conclusion': 'success', 'app': {'id': 5}}], workflows='source.yml')
        self.assertEqual(r.code, 0, r.log)
        self.assertEqual(r.main, r.ours)
        self.assertEqual(len(r.dispatches), 1)

    def test_dispatched_required_job_lands_as_a_commit_status(self):
        # MR 2026-11-17, run 44198313344: GitHub refused main although the dispatched check run passed,
        # because it counts no workflow_dispatch check run toward a ruleset. Only a commit status counts.
        for rule in ['source', {'context': 'source', 'integration_id': 15368}]:
            with self.subTest(rule=rule):
                r = self.land([['none']], required=[rule], checks=[{'name': 'source', 'conclusion': 'success', 'app': {'id': 15368}}], workflows='source.yml')
                self.assertEqual(r.code, 0, r.log)
                self.assertEqual(r.main, r.ours)
                self.assertEqual(r.posted, [['source', r.ours]])

    def test_actions_check_run_alone_never_satisfies_main(self):
        # The dispatched run passed but no job of it carries the required name: nothing GitHub counts exists.
        r = self.land([['none']], required=['source'], checks=[{'name': 'source', 'conclusion': 'success', 'app': {'id': 15368}}], jobs=[{'name': 'other', 'conclusion': 'success'}], workflows='source.yml', minutes=0)
        self.assertNotEqual(r.code, 0, r.log)
        self.assertEqual((r.main, r.posted), (r.base, []))

    def test_failed_or_skipped_job_is_never_reported(self):
        for state in ['skipped', 'failure']:
            with self.subTest(state):
                r = self.land([['none']], required=['source'], checks=[{'name': 'source', 'conclusion': state, 'app': {'id': 15368}}], workflows='source.yml', minutes=0)
                self.assertNotEqual(r.code, 0, r.log)
                self.assertEqual((r.main, r.posted), (r.base, []))

    def test_missing_skipped_failed_or_pending_required_check_holds(self):
        for state in [None, 'skipped', 'failure', 'neutral']:
            r = self.land([['none']], required=['source'], checks=[{'name': 'source', 'conclusion': state}], workflows='source.yml', minutes=0)
            self.assertNotEqual(r.code, 0, r.log)
            self.assertEqual(r.main, r.base)

    def test_optional_failure_blocks_successful_required_check(self):
        r = self.land([['success']], checks=[{'name': 'other', 'conclusion': 'failure'}], workflows='source.yml')
        self.assertNotEqual(r.code, 0, r.log)
        self.assertEqual(r.main, r.base)

    def test_new_dispatch_must_finish_even_if_same_sha_checks_already_pass(self):
        r = self.land([['success']], workflows='source.yml', dispatch_state='pending', minutes=0)
        self.assertNotEqual(r.code, 0, r.log)
        self.assertEqual(r.main, r.base)

    def test_unmigrated_caller_keeps_vercel_contract_without_checks_permission(self):
        r = self.land([['success']], extra_statuses=[{'context': 'advisory', 'state': 'failure'}])
        self.assertEqual(r.code, 0, r.log)

    def test_unmigrated_caller_cannot_skip_new_native_required_check(self):
        r = self.land([['success']], required=['source'], minutes=0)
        self.assertNotEqual(r.code, 0, r.log)
        self.assertEqual(r.main, r.base)

    def test_required_app_binding_rejects_same_named_status_or_other_app(self):
        for app in [None, {'id': 4}]:
            r = self.land([['none']], required=[{'context': 'source', 'integration_id': 5}], checks=[{'name': 'source', 'conclusion': 'success', 'app': app}], extra_statuses=[{'context': 'source', 'state': 'success'}], workflows='source.yml', minutes=0)
            self.assertNotEqual(r.code, 0, r.log)
            self.assertEqual(r.main, r.base)
        r = self.land([['none']], required=[{'context': 'source', 'integration_id': 5}], checks=[{'name': 'source', 'conclusion': 'success', 'app': {'id': 5}}], workflows='source.yml')
        self.assertEqual(r.code, 0, r.log)

    def test_legacy_vercel_still_required_when_another_native_context_passes(self):
        r = self.land([['none']], required=['source'], extra_statuses=[{'context': 'source', 'state': 'success'}], minutes=0)
        self.assertNotEqual(r.code, 0, r.log)
        self.assertEqual(r.main, r.base)

    def test_stale_dispatch_success_cannot_cover_missing_pending_or_failed_new_run(self):
        for state in ['missing', 'pending', 'failure']:
            r = self.land([['success']], workflows='source.yml', dispatch_state=state, minutes=0)
            self.assertNotEqual(r.code, 0, r.log)
            self.assertEqual(r.main, r.base)

    def test_dispatch_is_repeated_after_rebase(self):
        r = self.land([['success'], ['success']], workflows='source.yml', other='add')
        self.assertEqual(r.code, 0, r.log)
        self.assertEqual(len(r.dispatches), 2)


    def test_dropped_vercel_push_repushes_once_and_lands(self):
        # Paralax 2026-11-24, run 45454039738: GitHub dropped the push webhook for land/,
        # so Vercel never started a preview deployment and never posted a status.
        r = self.land([['pending', 'success']], minutes=5, drop_first_push=True, fake_clock=True)
        self.assertEqual(r.code, 0, r.log)
        self.assertEqual(r.main, r.ours)
        self.assertIsNone(r.land)
        self.assertIsNone(r.retry)
        self.assertEqual(r.outputs, {'sha': r.ours, 'landed': 'true'})
        self.assertTrue(all(x['land'] == r.ours for x in r.reads))
        self.assertEqual([x['retry'] for x in r.reads[:5]], [None] * 5)
        self.assertEqual([x['retry'] for x in r.reads[5:]], [r.ours, r.ours])
        self.assertEqual(r.log.count('pushing ' + BRANCH + '-retry once'), 1)

    def test_pending_vercel_status_never_repushes(self):
        r = self.land([['pending', 'pending', 'pending', 'pending', 'pending', 'success']], minutes=5, fake_clock=True)
        self.assertEqual(r.code, 0, r.log)
        self.assertEqual(r.main, r.ours)
        self.assertIsNone(r.retry)
        self.assertTrue(all(x['retry'] is None for x in r.reads))
        self.assertNotIn('-retry', r.log)

    def test_dropped_vercel_after_repush_still_fails_visibly(self):
        r = self.land([['none']], minutes=5, drop_first_push=True, fake_clock=True)
        self.assertEqual(r.code, 1, r.log)
        self.assertEqual((r.main, r.land, r.retry, r.outputs), (r.base, r.ours, None, {}))
        self.assertEqual(r.log.count('pushing ' + BRANCH + '-retry once'), 1)
        self.assertIn('checks still pending for ' + r.ours + ' after 5 min', r.log)


if __name__ == '__main__':
    unittest.main()
