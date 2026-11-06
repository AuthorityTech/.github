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
a = sys.argv[1:]
assert a[0] == 'api' and a[1].startswith('repos/' + os.environ['GITHUB_REPOSITORY'] + '/commits/') and '/statuses?' in a[1], a
sha = a[1].split('/commits/')[1].split('/')[0]
def ref(name):
    out = subprocess.run(['git', '--git-dir', os.environ['ORIGIN'], 'rev-parse', '-q', '--verify', name], capture_output=True, text=True).stdout.strip()
    return out or None
if sha not in f['seen']:
    f['seen'].append(sha)
build = f['seen'].index(sha)
n = sum(1 for r in f['reads'] if r['sha'] == sha)
f['reads'].append({'sha': sha, 'main': ref('refs/heads/main'), 'land': ref('refs/heads/' + os.environ['BRANCH'])})
if f['move_to'] and len(f['reads']) == 1:
    subprocess.run(['git', '--git-dir', os.environ['ORIGIN'], 'update-ref', 'refs/heads/main', f['move_to']], check=True)
(root / 'fixture.json').write_text(json.dumps(f))
states = f['builds'][min(build, len(f['builds']) - 1)][:n + 1]
statuses = [{'context': 'Vercel', 'state': s, 'target_url': 'https://vercel.example/' + s} for s in reversed(states) if s != 'none']
statuses.insert(1, {'context': 'jp-overlap', 'state': 'failure', 'target_url': None})
out = json.dumps(statuses)
if '--jq' in a:
    out = subprocess.run(['jq', '-r', a[a.index('--jq') + 1]], input=out, capture_output=True, text=True, check=True).stdout
sys.stdout.write(out)
'''


class LandChecked(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert shutil.which('jq'), 'the gh stub applies --jq with jq, as gh does'

    def land(self, builds, other=None, rounds=6, minutes=15, after_rebase=''):
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
        for name, body in [('gh', GH), ('sleep', '#!/bin/sh\nexit 0\n')]:
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        (d / 'fixture.json').write_text(json.dumps(dict(builds=builds, move_to=move_to, seen=[], reads=[])))
        output = d / 'output'
        output.write_text('')
        env.update(PATH=str(stubs) + os.pathsep + env['PATH'], FIXTURE=str(d), ORIGIN=str(origin), BRANCH=BRANCH,
                   GITHUB_REPOSITORY=REPO, GITHUB_RUN_ID='7', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_WORKFLOW_REF=REPO + '/.github/workflows/producer.yml@refs/heads/main',
                   GITHUB_OUTPUT=str(output), GH_TOKEN='local-fixture-only',
                   BUILD_MINUTES=str(minutes), ROUNDS=str(rounds), AFTER_REBASE=after_rebase)
        result = subprocess.run(['bash', str(SCRIPT)], cwd=work, env=env, capture_output=True, text=True, timeout=60)
        f = json.loads((d / 'fixture.json').read_text())

        def at(name):
            r = subprocess.run(['git', '--git-dir', str(origin), 'rev-parse', '-q', '--verify', name], env=env, capture_output=True, text=True)
            return r.stdout.strip() or None

        return SimpleNamespace(
            code=result.returncode, log=result.stdout + result.stderr, base=base, ours=ours, other=move_to,
            main=at('refs/heads/main'), land=at('refs/heads/' + BRANCH), seen=f['seen'], reads=f['reads'],
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
        self.assertIn('Vercel still pending for ' + r.ours + ' after 0 min', r.log)

    def test_failure_or_error_leaves_main_untouched(self):
        for state in ['failure', 'error']:
            with self.subTest(state):
                r = self.land([['pending', state]])
                self.assertEqual(r.code, 1, r.log)
                self.assertEqual((r.main, r.land, r.outputs), (r.base, r.ours, {}))
                self.assertIn('Vercel reported ' + state, r.log)

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


if __name__ == '__main__':
    unittest.main()
