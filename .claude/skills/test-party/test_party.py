"""CLI tests for party.py, run against a throwaway repo with a bare origin and stubbed gh, npm and npx.

Run: python3 -m unittest discover -s .claude/skills/test-party
"""
import csv
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PARTY_SOURCE = Path(__file__).with_name('party.py').resolve()
PARTY = '2026-01-01-trial'
PLAN = textwrap.dedent('''\
    Link Unfurling
    ID,Scenario,Action,Steps,Expected Result
    LU-001,Unfurl,Paste a design link,1. Paste a link,A card appears
    LU-002,Unfurl,Paste a folder link,1. Paste a folder link,A folder card appears
''')
HASHES = {'LU-001': '4d7205fb152e', 'LU-002': '94a0ad5b6833'}
HEADER_WITHOUT_METHOD = 'case_id,result,case_hash,app_version,deploy_sha,timestamp,note'
GH_STUB = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    from pathlib import Path
    state = Path(os.environ['GH_STUB_DIR'])
    args = sys.argv[1:]
    # The party's PR is #7, in body.md; a Case Spec branch's PR is in spec-prs.json, keyed by branch.
    spec_prs_file = state / 'spec-prs.json'
    spec_prs = json.loads(spec_prs_file.read_text()) if spec_prs_file.exists() else {}
    if args[:2] == ['pr', 'view'] and args[2].startswith('test-party/'):
        body = state / 'body.md'
        print(json.dumps({'number': 7, 'state': 'OPEN', 'body': body.read_text() if body.exists() else ''}))
    elif args[:2] == ['pr', 'view']:
        if args[2] not in spec_prs:
            sys.exit(f'no pull requests found for branch "{args[2]}"')
        print(json.dumps(spec_prs[args[2]]))
    elif args[:2] == ['pr', 'create']:
        head = args[args.index('--head') + 1]
        number = 8 + len(spec_prs)
        spec_prs[head] = {'number': number, 'state': 'OPEN', 'url': f'https://github.test/pull/{number}',
                          'base': args[args.index('--base') + 1], 'title': args[args.index('--title') + 1],
                          'body': args[args.index('--body') + 1]}
        spec_prs_file.write_text(json.dumps(spec_prs))
        print(spec_prs[head]['url'])
    elif args[:2] == ['pr', 'edit'] and args[2] == '7':
        (state / 'body.md').write_text(args[args.index('--body') + 1])
    elif args[:2] == ['pr', 'edit']:
        pr = next(pr for pr in spec_prs.values() if str(pr['number']) == args[2])
        pr['body'] = args[args.index('--body') + 1]
        spec_prs_file.write_text(json.dumps(spec_prs))
    elif args[:2] == ['run', 'list']:
        print((state / 'runs.json').read_text())
    else:
        sys.exit(f'gh stub: unsupported {args}')
''')
# Each preflight call consumes the next canned report in preflight.json.
NPM_STUB = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    from pathlib import Path
    state = Path(os.environ['GH_STUB_DIR'])
    if 'typecheck' in sys.argv:
        sys.exit("src/teams.ts(1,1): error TS2304: Cannot find name 'oops'." if (state / 'typecheck-fails').exists() else 0)
    if 'preflight' not in sys.argv:
        sys.exit(f'npm stub: unsupported {sys.argv[1:]}')
    reports = json.loads((state / 'preflight.json').read_text())
    with (state / 'calls.log').open('a') as log:
        log.write('preflight\\n')
    print(json.dumps(reports.pop(0)))
    (state / 'preflight.json').write_text(json.dumps(reports))
''')
# Husky's hook runner: it fails every commit, as the app's pre-commit hook does in a party worktree, unless HUSKY=0.
HUSKY_PRE_COMMIT = textwrap.dedent('''\
    #!/bin/sh
    [ "${HUSKY-}" = "0" ] && exit 0
    echo 'jest: No tests found' >&2
    exit 1
''')
# Replays canned Playwright JSON reporter output for the one spec file it is given.
NPX_STUB = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    from pathlib import Path
    state = Path(os.environ['GH_STUB_DIR'])
    args = sys.argv[1:]
    if args[:2] != ['playwright', 'test'] or '--reporter=json' not in args:
        sys.exit(f'npx stub: unsupported {args}')
    spec = next(a for a in args[2:] if not a.startswith('-'))
    with (state / 'calls.log').open('a') as log:
        log.write(f'playwright {spec}\\n')
    outcome = json.loads((state / 'outcomes.json').read_text())[spec]
    if outcome == 'crashed':
        sys.exit('Error: cannot find module\\n    at somewhere')
    passed = outcome == 'passed'
    # 'canva-failed' fails inside a Canva helper; 'cooldown' there too, refused a repeat mention; 'failed' in the spec.
    location = ({'file': os.path.join(os.getcwd(), 'src', 'canvaDesign.ts'), 'line': 126, 'column': 3}
                if outcome in ('canva-failed', 'cooldown') else
                {'file': os.path.join(os.getcwd(), spec), 'line': 9, 'column': 5})
    message = ('Error: Canva sent the mention notification\\n\\nLocator: getByText(/You already notified/)\\n'
               if outcome == 'cooldown' else
               '\\u001b[31mTimeoutError: locator.click: Timeout 30000ms exceeded.\\u001b[39m\\n'
               'Call log:\\n\\u001b[2m  - waiting for getByRole(\\'button\\', { name: \\'Connect\\' })\\u001b[22m\\n')
    output = next((a.split('=', 1)[1] for a in args if a.startswith('--output=')), 'test-results')
    output_dir = os.path.join(os.getcwd(), output, os.path.basename(spec).split('.')[0] + '-chromium')
    result = {'status': 'passed', 'errors': [], 'attachments': []} if passed else {
        'status': 'failed',
        'errors': [{'message': message, 'location': location}],
        'attachments': [{'name': 'error-context', 'contentType': 'text/markdown',
                         'path': os.path.join(output_dir, 'error-context.md')}]}
    print(json.dumps({'suites': [{'specs': [{'tests': [{'results': [result]}]}]}], 'errors': [],
                      'stats': {'expected': int(passed), 'unexpected': int(not passed), 'flaky': 0, 'skipped': 0}}))
    sys.exit(0 if passed else 1)
''')


AUTOMATION_CONFIG = {
    'commit_env': {'HUSKY': '0'},
    'automation': {
        'shared_paths': ['node_modules', 'e2e/personas.local.ts'],
        'typecheck': ['npm', 'run', '--silent', 'typecheck', '-w', 'e2e'],
        'preflight': ['npm', 'run', '--silent', 'preflight', '-w', 'e2e'],
        'setup_helpers': r'canva\w*\.ts',
        'setup_label': 'Canva',
        'transient_errors': ['You already notified'],
        'transient_label': 'Canva refused a repeat mention',
    },
}


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class PartyCli(unittest.TestCase):
    CONFIG = AUTOMATION_CONFIG
    BASE = 'main'
    PLANS_DIR = 'docs/test-plans'

    def install_party(self, config):
        tool = self.root / 'tool'
        tool.mkdir(exist_ok=True)
        shutil.copy(PARTY_SOURCE, tool / 'party.py')
        (tool / 'test-party.json').write_text(json.dumps(config))
        return tool / 'party.py'

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        origin, seed, self.repo, bin_dir = (self.root / d for d in ('origin.git', 'seed', 'repo', 'bin'))
        git('init', '--quiet', '--bare', f'--initial-branch={self.BASE}', str(origin), cwd=self.root)
        git('clone', '--quiet', str(origin), str(seed), cwd=self.root)
        (seed / self.PLANS_DIR).mkdir(parents=True)
        (seed / self.PLANS_DIR / 'link-unfurling.csv').write_text(PLAN)
        self.commit_all(seed, 'seed plans')
        git('push', '--quiet', 'origin', f'HEAD:{self.BASE}', cwd=seed)
        git('clone', '--quiet', str(origin), str(self.repo), cwd=self.root)
        bin_dir.mkdir()
        for name, stub in (('gh', GH_STUB), ('npm', NPM_STUB), ('npx', NPX_STUB)):
            (bin_dir / name).write_text(stub)
            (bin_dir / name).chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
                    'GH_STUB_DIR': str(self.root), 'GIT_AUTHOR_NAME': 'Tester',
                    'GIT_AUTHOR_EMAIL': 'tester@example.com', 'GIT_COMMITTER_NAME': 'Tester',
                    'GIT_COMMITTER_EMAIL': 'tester@example.com'}
        self.env.pop('TEST_PARTY_CASES_REF', None)
        self.party_py = self.install_party(self.CONFIG)
        self.hooks = self.root / 'hooks'
        self.add_hook('pre-commit', HUSKY_PRE_COMMIT)
        git('config', 'core.hooksPath', str(self.hooks), cwd=self.repo)
        self.party('join', PARTY)

    def add_hook(self, name, source):
        self.hooks.mkdir(exist_ok=True)
        (self.hooks / name).write_text(source)
        (self.hooks / name).chmod(0o755)

    def commit_all(self, cwd, message):
        git('add', '.', cwd=cwd)
        git('-c', 'user.name=Tester', '-c', 'user.email=tester@example.com',
            'commit', '--quiet', '-m', message, cwd=cwd)

    def run_party(self, *args):
        return subprocess.run([sys.executable, str(self.party_py), *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)

    def party(self, *args):
        done = self.run_party(*args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def merge_specs(self, specs):
        """Pushes Case Specs to main, as {path under e2e/specs: source}, and pulls them into the working copy."""
        seed = self.root / 'seed'
        git('pull', '--quiet', cwd=seed)
        for path, source in specs.items():
            (seed / 'e2e/specs' / path).parent.mkdir(parents=True, exist_ok=True)
            (seed / 'e2e/specs' / path).write_text(source)
        self.commit_all(seed, 'merge case specs')
        git('push', '--quiet', 'origin', f'HEAD:{self.BASE}', cwd=seed)
        git('pull', '--quiet', cwd=self.repo)

    def stub_runs(self, preflights, outcomes=None):
        (self.root / 'preflight.json').write_text(json.dumps(preflights))
        (self.root / 'outcomes.json').write_text(json.dumps(outcomes or {}))

    def calls(self):
        log = self.root / 'calls.log'
        return log.read_text().splitlines() if log.exists() else []

    def run_specs(self, *extra):
        return self.run_party('run', PARTY, '--tester', 'josh', '--app-version', '1.0.0',
                              '--deploy-sha', 'abc123', *extra)

    def recorded(self, tester='josh'):
        try:
            return [(r['case_id'], r['result'], r['method'])
                    for r in csv.DictReader(io.StringIO(self.pushed_log(tester)))]
        except subprocess.CalledProcessError:
            return []

    def record(self, case_id, result, *extra):
        return self.party('record', PARTY, case_id, result, '--tester', 'josh',
                          '--app-version', '1.0.0', '--deploy-sha', 'abc123', *extra)

    def pushed_log(self, tester='josh'):
        return git('show', f'origin/test-party/{PARTY}:docs/test-parties/{PARTY}/{tester}.csv', cwd=self.repo)

    def push_log_without_method(self, *rows):
        other = self.root / 'other'
        git('clone', '--quiet', '--branch', f'test-party/{PARTY}', str(self.root / 'origin.git'), str(other),
            cwd=self.root)
        (other / f'docs/test-parties/{PARTY}/josh.csv').write_text('\n'.join([HEADER_WITHOUT_METHOD, *rows]) + '\n')
        self.commit_all(other, 'log without method')
        git('push', '--quiet', cwd=other)

    def test_record_commits_past_the_app_pre_commit_hook(self):
        self.record('LU-001', 'Passed')

        self.assertEqual(self.recorded(), [('LU-001', 'Passed', 'agent')])

    def test_record_puts_the_log_back_when_its_commit_fails(self):
        self.record('LU-001', 'Passed')
        log = self.repo / f'.git/test-party-worktrees/{PARTY}/docs/test-parties/{PARTY}/josh.csv'
        before = log.read_text()
        self.add_hook('commit-msg', '#!/bin/sh\nexit 1\n')

        done = self.run_party('record', PARTY, 'LU-002', 'Passed', '--tester', 'josh',
                              '--app-version', '1.0.0', '--deploy-sha', 'abc123')

        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(log.read_text(), before)
        self.assertEqual(git('status', '--porcelain', cwd=log.parent), '')
        (self.hooks / 'commit-msg').unlink()
        self.record('LU-002', 'Passed')
        self.assertEqual(self.recorded(), [('LU-001', 'Passed', 'agent'), ('LU-002', 'Passed', 'agent')])

    def test_record_writes_method_column_to_a_new_log(self):
        self.record('LU-001', 'Passed')

        rows = list(csv.DictReader(io.StringIO(self.pushed_log())))
        self.assertEqual([(r['case_id'], r['result'], r['method']) for r in rows],
                         [('LU-001', 'Passed', 'agent')])

    def test_import_records_the_given_method_on_every_row(self):
        results = self.root / 'spec-results.csv'
        results.write_text('case_id,result,case_hash\n'
                           f'LU-001,Passed,{HASHES["LU-001"]}\nLU-002,Passed,{HASHES["LU-002"]}\n')

        self.party('import', PARTY, 'josh', str(results), '--method', 'spec',
                   '--app-version', '1.0.0', '--deploy-sha', 'abc123')

        rows = list(csv.DictReader(io.StringIO(self.pushed_log())))
        self.assertEqual([(r['case_id'], r['method']) for r in rows], [('LU-001', 'spec'), ('LU-002', 'spec')])

    def test_import_defaults_to_agent(self):
        results = self.root / 'results.csv'
        results.write_text(f'case_id,result,case_hash\nLU-001,Blocked,{HASHES["LU-001"]}\n')

        self.party('import', PARTY, 'josh', str(results), '--app-version', '1.0.0', '--deploy-sha', 'abc123')

        rows = list(csv.DictReader(io.StringIO(self.pushed_log())))
        self.assertEqual([(r['case_id'], r['method']) for r in rows], [('LU-001', 'agent')])

    def test_recording_to_a_log_without_method_adds_the_column_and_keeps_earlier_rows_aligned(self):
        self.push_log_without_method(
            f'LU-001,Failed,{HASHES["LU-001"]},0.9.0,old111,2026-01-01T00:00:00+00:00,"card missing, retry"')

        self.record('LU-002', 'Passed', '--method', 'spec')

        rows = list(csv.DictReader(io.StringIO(self.pushed_log())))
        self.assertEqual(
            [(r['case_id'], r['result'], r['app_version'], r['note'], r['method']) for r in rows],
            [('LU-001', 'Failed', '0.9.0', 'card missing, retry', 'agent'),
             ('LU-002', 'Passed', '1.0.0', '', 'spec')])

    def test_status_and_pr_section_read_a_log_without_method(self):
        self.push_log_without_method(
            f'LU-001,Passed,{HASHES["LU-001"]},0.9.0,old111,2026-01-01T00:00:00+00:00,',
            f'LU-002,Failed,{HASHES["LU-002"]},0.9.0,old111,2026-01-02T00:00:00+00:00,no card')

        status = json.loads(self.party('status', PARTY, '--app-version', '1.0.0', '--deploy-sha', 'abc123'))
        self.party('pr', PARTY)

        self.assertEqual(status['features'], {'link-unfurling': {'title': 'Link Unfurling', 'passed': 1,
                                                                 'failed-retest': 1}})
        self.assertEqual([(r['id'], r['last']) for r in status['remaining']],
                         [('LU-002', 'Failed by josh: no card')])
        body = (self.root / 'body.md').read_text()
        self.assertIn('Last result 2026-01-02T00:00:00+00:00, on app 0.9.0 · deploy old111.', body)
        self.assertIn('| Link Unfurling | 1 | 1 | – | – | – |', body)

    def unspecced_ids(self):
        status = json.loads(self.party('status', PARTY, '--unspecced'))
        return [case['id'] for case in status['unspecced']]

    def test_status_unspecced_lists_passed_cases_without_a_case_spec(self):
        self.record('LU-001', 'Passed')
        self.record('LU-002', 'Failed')

        self.assertEqual(self.unspecced_ids(), ['LU-001'])

    def test_status_unspecced_skips_a_case_specced_on_main(self):
        self.record('LU-001', 'Passed')
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'])})

        self.assertEqual(self.unspecced_ids(), [])

    def test_status_unspecced_skips_a_case_specced_on_a_spec_branch(self):
        self.record('LU-001', 'Passed')
        tree = self.spec_tree()
        self.write_spec('LU-001', HASHES['LU-001'])
        git('add', '.', cwd=tree)
        git('-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Tester', '-c', 'user.email=tester@example.com',
            'commit', '--quiet', '-m', 'keep LU-001', cwd=tree)
        git('push', '--quiet', 'origin', f'HEAD:{self.spec_branch()}', cwd=tree)

        self.assertEqual(self.unspecced_ids(), [])

    def test_status_unspecced_lists_a_case_specced_only_on_a_deleted_spec_branch(self):
        self.test_status_unspecced_skips_a_case_specced_on_a_spec_branch()
        origin = git('remote', 'get-url', 'origin', cwd=self.repo).strip()
        git('--git-dir', origin, 'branch', '--quiet', '-D', self.spec_branch(), cwd=self.repo)

        self.assertEqual(self.unspecced_ids(), ['LU-001'])

    def test_status_unspecced_lists_a_case_whose_spec_is_stale(self):
        self.record('LU-001', 'Passed')
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', '000000000000')})

        self.assertEqual(self.unspecced_ids(), ['LU-001'])

    def test_show_prints_the_case_spec_marker_with_the_case_hash(self):
        self.assertIn(f"caseSpec('LU-001', '{HASHES['LU-001']}')", self.party('show', 'LU-001'))

    def test_show_finds_cases_when_run_from_a_subdirectory(self):
        subdir = self.repo / 'docs'
        done = subprocess.run([sys.executable, str(self.party_py), 'show', 'LU-001'], cwd=subdir, env=self.env,
                              capture_output=True, text=True)

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('LU-001', done.stdout)

    def push_to_main(self, path, text):
        seed = self.root / 'seed'
        git('pull', '--quiet', cwd=seed)
        (seed / path).parent.mkdir(parents=True, exist_ok=True)
        (seed / path).write_text(text)
        self.commit_all(seed, f'change {path}')
        git('push', '--quiet', 'origin', f'HEAD:{self.BASE}', cwd=seed)
        return git('rev-parse', 'HEAD', cwd=seed).strip()

    def pushed_party_head(self):
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        return git('rev-parse', f'origin/test-party/{PARTY}', cwd=self.repo).strip()

    def is_ancestor(self, older, newer):
        return subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer], cwd=self.repo).returncode == 0

    def test_sync_merges_main_into_the_party_branch_and_pushes_without_rewriting_it(self):
        self.record('LU-001', 'Passed')
        party_head = self.pushed_party_head()
        main_head = self.push_to_main('README.md', 'new on main\n')

        self.party('sync', PARTY)

        synced = self.pushed_party_head()
        self.assertTrue(self.is_ancestor(party_head, synced))
        self.assertTrue(self.is_ancestor(main_head, synced))

    def test_sync_with_nothing_new_on_main_says_so_and_commits_nothing(self):
        party_head = self.pushed_party_head()

        out = self.party('sync', PARTY)

        self.assertIn('already has everything on origin/main', out)
        self.assertEqual(self.pushed_party_head(), party_head)

    def test_sync_that_conflicts_aborts_the_merge_and_leaves_the_branch_as_it_was(self):
        self.record('LU-001', 'Passed')
        party_head = self.pushed_party_head()
        self.push_to_main(f'docs/test-parties/{PARTY}/josh.csv', 'conflicting line\n')
        tree = self.repo / '.git/test-party-worktrees' / PARTY

        done = self.run_party('sync', PARTY)

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('conflict', done.stderr)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=tree).strip(), party_head)
        self.assertEqual(git('status', '--porcelain', cwd=tree), '')
        self.assertEqual(self.pushed_party_head(), party_head)


    def test_run_stops_without_running_specs_when_preflight_fails(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'])})
        self.stub_runs([preflight(ok=False)], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.run_specs()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('teams-user signed-in', done.stderr)
        self.assertEqual(self.calls(), ['preflight'])
        self.assertEqual(self.recorded(), [])

    def test_run_ignores_an_unmerged_spec_and_records_passes_as_spec(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'])})
        (self.repo / 'e2e/specs/link-unfurling/LU-002.spec.ts').write_text(case_spec('LU-002', HASHES['LU-002']))
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['preflight', 'playwright specs/link-unfurling/LU-001.spec.ts'])
        self.assertEqual(self.recorded(), [('LU-001', 'Passed', 'spec')])
        self.assertEqual(json.loads(done.stdout)['passed'], ['LU-001'])

    def test_run_skips_a_merged_spec_whose_working_copy_differs_from_main(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001']),
                          'link-unfurling/LU-002.spec.ts': case_spec('LU-002', HASHES['LU-002'])})
        with (self.repo / 'e2e/specs/link-unfurling/LU-001.spec.ts').open('a') as spec:
            spec.write('// an unreviewed edit\n')
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('playwright specs/link-unfurling/LU-001.spec.ts', self.calls())
        self.assertEqual(self.recorded(), [('LU-002', 'Passed', 'spec')])
        self.assertEqual(json.loads(done.stdout)['differs_from_main'], ['LU-001'])

    def test_run_excludes_a_stale_spec(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001']),
                          'link-unfurling/LU-002.spec.ts': case_spec('LU-002', 'aaaaaaaaaaaa')})
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('playwright specs/link-unfurling/LU-002.spec.ts', self.calls())
        self.assertEqual(self.recorded(), [('LU-001', 'Passed', 'spec')])
        self.assertEqual(json.loads(done.stdout)['stale'], ['LU-002'])

    def test_run_records_nothing_for_a_failing_spec_and_prints_its_case_id(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001']),
                          'link-unfurling/LU-002.spec.ts': case_spec('LU-002', HASHES['LU-002'])})
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'failed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.recorded(), [('LU-002', 'Passed', 'spec')])
        self.assertEqual(json.loads(done.stdout)['failed'], [{
            'id': 'LU-001',
            'error': "TimeoutError: locator.click: Timeout 30000ms exceeded. "
                     "Waiting for getByRole('button', { name: 'Connect' })",
            'kind': 'product',
            'artifacts': 'e2e/test-results/LU-001/LU-001-chromium'}])

    def test_run_names_the_canva_helper_a_spec_broke_in(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'])})
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'canva-failed'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        failed, = json.loads(done.stdout)['failed']
        self.assertEqual((failed['kind'], failed['helper']), ('setup', 'src/canvaDesign.ts:126'))

    def test_run_reports_a_spec_canva_refused_a_repeat_mention_as_a_cooldown(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'])})
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'cooldown'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        failed, = json.loads(done.stdout)['failed']
        self.assertEqual(failed['kind'], 'transient')
        self.assertNotIn('helper', failed)

    def test_run_reports_a_spec_that_produced_no_report_as_failed(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'])})
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'crashed'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.recorded(), [])
        self.assertEqual(json.loads(done.stdout)['failed'], [{
            'id': 'LU-001', 'error': 'spec runner exited 1 without a report: Error: cannot find module',
            'kind': 'product', 'artifacts': None}])

    def test_run_reruns_preflight_after_a_baseline_changing_spec_and_continues_when_it_passes(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'], changes_baseline=True),
                          'link-unfurling/LU-002.spec.ts': case_spec('LU-002', HASHES['LU-002'])})
        self.stub_runs([preflight(), preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                                    'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.run_specs()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['preflight', 'playwright specs/link-unfurling/LU-001.spec.ts',
                                        'preflight', 'playwright specs/link-unfurling/LU-002.spec.ts'])
        self.assertEqual(self.recorded(), [('LU-001', 'Passed', 'spec'), ('LU-002', 'Passed', 'spec')])

    def test_run_stops_when_preflight_fails_after_a_baseline_changing_spec(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'], changes_baseline=True),
                          'link-unfurling/LU-002.spec.ts': case_spec('LU-002', HASHES['LU-002'])})
        self.stub_runs([preflight(), preflight(ok=False)], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                                            'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.run_specs()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('teams-user signed-in', done.stderr)
        self.assertNotIn('playwright specs/link-unfurling/LU-002.spec.ts', self.calls())
        self.assertEqual(self.recorded(), [('LU-001', 'Passed', 'spec')])
        self.assertEqual(json.loads(done.stdout)['not_run'], ['LU-002'])

    def test_import_rejects_a_spec_result_whose_hash_differs_from_main(self):
        results = self.root / 'spec-results.csv'
        results.write_text('case_id,result,case_hash\nLU-001,Passed,aaaaaaaaaaaa\n')

        done = self.run_party('import', PARTY, 'josh', str(results), '--method', 'spec',
                              '--app-version', '1.0.0', '--deploy-sha', 'abc123')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('LU-001 text differs from origin/main', done.stderr)
        self.assertEqual(self.recorded(), [])

    def spec_branch(self):
        return f'test/case-specs/{PARTY}/josh-{datetime.date.today().isoformat()}'

    def spec_tree(self):
        return Path(self.party('spec-branch', PARTY, '--tester', 'josh').strip())

    def test_spec_branch_is_a_worktree_off_main_reused_for_the_rest_of_the_session(self):
        tree = self.spec_tree()

        self.assertEqual(git('branch', '--show-current', cwd=tree).strip(), self.spec_branch())
        self.assertEqual(git('rev-parse', 'HEAD', cwd=tree), git('rev-parse', 'origin/main', cwd=self.repo))
        self.assertEqual(self.spec_tree(), tree)

    def write_spec(self, case_id, case_hash, **options):
        spec = self.spec_tree() / f'e2e/specs/link-unfurling/{case_id}.spec.ts'
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(case_spec(case_id, case_hash, **options))
        return spec

    def keep_spec(self, case_id):
        return self.run_party('keep-spec', PARTY, case_id, '--tester', 'josh',
                              '--app-version', '1.0.0', '--deploy-sha', 'abc123')

    def spec_prs(self):
        prs = self.root / 'spec-prs.json'
        return json.loads(prs.read_text()) if prs.exists() else {}

    def pushed_spec_files(self):
        try:
            listing = git('ls-tree', '-r', '--name-only', f'origin/{self.spec_branch()}', '--', 'e2e/specs',
                          cwd=self.repo)
        except subprocess.CalledProcessError:
            return []
        return listing.split()

    def test_keep_spec_replays_a_spec_from_an_agent_pass_alone_and_opens_a_pr_off_main(self):
        (self.repo / 'node_modules').mkdir()
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['playwright specs/link-unfurling/LU-001.spec.ts'])
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        self.assertEqual(self.pushed_spec_files(), ['e2e/specs/link-unfurling/LU-001.spec.ts'])
        self.assertNotIn('node_modules', git('ls-tree', '--name-only', f'origin/{self.spec_branch()}', cwd=self.repo))
        self.assertEqual(git('merge-base', f'origin/{self.spec_branch()}', 'origin/main', cwd=self.repo),
                         git('rev-parse', 'origin/main', cwd=self.repo))
        pr = self.spec_prs()[self.spec_branch()]
        self.assertEqual(pr['base'], 'main')
        self.assertIn('LU-001 (new)', pr['body'])
        self.assertIn(pr['url'], done.stdout)
        party_files = git('ls-tree', '-r', '--name-only', f'origin/test-party/{PARTY}', cwd=self.repo).split()
        self.assertFalse([f for f in party_files if f.startswith('e2e/')])

    def assert_no_spec_kept_from(self, case_id, result, method='agent'):
        self.record(case_id, result, '--method', method)
        self.write_spec(case_id, HASHES[case_id])
        self.stub_runs([], {f'specs/link-unfurling/{case_id}.spec.ts': 'passed'})

        done = self.keep_spec(case_id)

        self.assertNotEqual(done.returncode, 0)
        self.assertIn(f'latest: {result} by {method}', done.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.spec_prs(), {})

    def test_keep_spec_refuses_a_failed_case(self):
        self.assert_no_spec_kept_from('LU-001', 'Failed')

    def test_keep_spec_refuses_a_blocked_case(self):
        self.assert_no_spec_kept_from('LU-001', 'Blocked')

    def test_keep_spec_refuses_a_case_last_passed_by_its_spec(self):
        self.assert_no_spec_kept_from('LU-001', 'Passed', method='spec')

    def test_keep_spec_refuses_a_spec_marked_with_a_hash_other_than_the_case_on_main(self):
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', 'aaaaaaaaaaaa')
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn(f'case on origin/main is {HASHES["LU-001"]}', done.stderr)
        self.assertEqual(self.calls(), [])

    def test_keep_spec_stops_before_replaying_a_spec_that_fails_the_e2e_typecheck(self):
        self.record('LU-001', 'Passed')
        spec = self.write_spec('LU-001', HASHES['LU-001'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})
        (self.root / 'typecheck-fails').touch()

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("npm run --silent typecheck -w e2e failed", done.stderr)
        self.assertIn("Cannot find name 'oops'", done.stderr)
        self.assertEqual(self.calls(), [])
        self.assertTrue(spec.exists())
        self.assertEqual(self.spec_prs(), {})

    def test_keep_spec_commits_past_the_app_pre_commit_hook(self):
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        self.assertEqual(self.pushed_spec_files(), ['e2e/specs/link-unfurling/LU-001.spec.ts'])

    def test_keep_spec_discards_a_spec_that_fails_its_solo_replay(self):
        self.record('LU-001', 'Passed')
        spec = self.write_spec('LU-001', HASHES['LU-001'], imports=['teams'])
        helper = self.write_helper('teams.ts')
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'failed'})

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('LU-001 spec failed its solo replay, so it was discarded: TimeoutError', done.stderr)
        self.assertFalse(spec.exists())
        self.assertFalse(helper.exists())
        self.assertEqual(self.pushed_spec_files(), [])
        self.assertEqual(self.spec_prs(), {})

    def test_keep_spec_saves_what_it_discards_as_a_patch_that_restores_it(self):
        self.record('LU-001', 'Passed')
        spec = self.write_spec('LU-001', HASHES['LU-001'], imports=['teams'])
        self.write_helper('teams.ts')
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'failed'})

        done = self.keep_spec('LU-001')

        patch = re.search(r'saved as (\S+\.patch);', done.stderr).group(1)
        git('apply', patch, cwd=self.spec_tree())
        self.assertEqual(spec.read_text(), case_spec('LU-001', HASHES['LU-001'], imports=['teams']))
        self.assertEqual((self.spec_tree() / 'e2e/src/teams.ts').read_text(), 'export const changed = 1;\n')

    def test_keep_spec_commits_only_its_spec_and_the_changed_helpers_it_imports(self):
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'], imports=['teamsShare'])
        self.write_helper('teamsShare.ts', "import { changed } from './teams.ts';\n")
        self.write_helper('teams.ts')
        unimported = self.write_helper('canvaSharing.ts')
        waiting = self.write_spec('LU-002', HASHES['LU-002'], imports=['canvaSharing'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['playwright specs/link-unfurling/LU-001.spec.ts'])
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        committed = git('ls-tree', '-r', '--name-only', f'origin/{self.spec_branch()}', '--', 'e2e',
                        cwd=self.repo).split()
        self.assertEqual(committed, ['e2e/specs/link-unfurling/LU-001.spec.ts', 'e2e/src/teams.ts',
                                     'e2e/src/teamsShare.ts'])
        self.assertTrue(unimported.exists())
        self.assertTrue(waiting.exists())

    def test_keep_spec_refuses_a_spec_already_kept(self):
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})
        self.assertEqual(self.keep_spec('LU-001').returncode, 0)

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('nothing to keep', done.stderr)
        self.assertEqual(self.calls(), ['playwright specs/link-unfurling/LU-001.spec.ts'])

    def test_keep_spec_that_fails_leaves_other_specs_waiting_in_the_worktree(self):
        self.record('LU-001', 'Passed')
        spec = self.write_spec('LU-001', HASHES['LU-001'])
        waiting = self.write_spec('LU-002', HASHES['LU-002'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'failed'})

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(spec.exists())
        self.assertTrue(waiting.exists())

    def test_keep_spec_replays_no_uncommitted_spec_using_a_helper_it_changes(self):
        self.merge_spec_using_a_helper()
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'], imports=['canvaDesign'])
        self.write_helper('canvaDesign.ts')
        (self.spec_tree() / 'e2e/specs/link-unfurling/LU-002.spec.ts').write_text(
            case_spec('LU-002', HASHES['LU-002'], imports=['canvaDesign', 'teams']))
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['playwright specs/link-unfurling/LU-001.spec.ts'])

    def test_keep_spec_names_the_canva_helper_a_discarded_spec_broke_in(self):
        self.record('LU-001', 'Passed')
        spec = self.write_spec('LU-001', HASHES['LU-001'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'canva-failed'})

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('discarded: TimeoutError', done.stderr)
        self.assertIn('(in Canva helper src/canvaDesign.ts:126)', done.stderr)
        self.assertFalse(spec.exists())

    def test_keep_spec_adds_later_specs_in_the_session_to_the_same_pr(self):
        self.record('LU-001', 'Passed')
        self.record('LU-002', 'Passed')
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                            'specs/link-unfurling/LU-002.spec.ts': 'passed'})
        self.write_spec('LU-001', HASHES['LU-001'])
        self.assertEqual(self.keep_spec('LU-001').returncode, 0)
        self.write_spec('LU-002', HASHES['LU-002'])

        done = self.keep_spec('LU-002')

        self.assertEqual(done.returncode, 0, done.stderr)
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        self.assertEqual(self.pushed_spec_files(), ['e2e/specs/link-unfurling/LU-001.spec.ts',
                                                    'e2e/specs/link-unfurling/LU-002.spec.ts'])
        prs = self.spec_prs()
        self.assertEqual(list(prs), [self.spec_branch()])
        self.assertIn('LU-001 (new)', prs[self.spec_branch()]['body'])
        self.assertIn('LU-002 (new)', prs[self.spec_branch()]['body'])

    def test_keep_spec_rewrites_a_merged_spec_that_is_stale_or_failed(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', 'aaaaaaaaaaaa')})
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'])
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('LU-001 (rewritten)', self.spec_prs()[self.spec_branch()]['body'])
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        self.assertIn('rewrite case spec LU-001', git('log', '-1', '--format=%s', f'origin/{self.spec_branch()}',
                                                      cwd=self.repo))

    def test_keep_spec_reruns_preflight_after_a_baseline_changing_spec(self):
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'], changes_baseline=True)
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['playwright specs/link-unfurling/LU-001.spec.ts', 'preflight'])

    def test_keep_spec_discards_a_baseline_changing_spec_whose_teardown_left_the_baseline_broken(self):
        self.record('LU-001', 'Passed')
        spec = self.write_spec('LU-001', HASHES['LU-001'], changes_baseline=True)
        self.stub_runs([preflight(ok=False)], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('baseline not restored after LU-001', done.stderr)
        self.assertIn('teams-user signed-in', done.stderr)
        self.assertFalse(spec.exists())
        self.assertEqual(self.spec_prs(), {})

    def merge_spec_using_a_helper(self):
        """LU-002 is merged and imports canvaDesign, so a helper change made while keeping LU-001 reaches it."""
        self.merge_specs({
            'link-unfurling/LU-002.spec.ts': case_spec('LU-002', HASHES['LU-002'], imports=['canvaDesign']),
            '../src/canvaDesign.ts': 'export const changed = 0;\n'})

    def test_keep_spec_also_replays_every_merged_spec_using_a_helper_it_changes(self):
        self.merge_spec_using_a_helper()
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'], imports=['canvaDesign'])
        self.write_helper('canvaDesign.ts')
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                            'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['playwright specs/link-unfurling/LU-001.spec.ts',
                                        'playwright specs/link-unfurling/LU-002.spec.ts'])
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        self.assertEqual(git('show', f'origin/{self.spec_branch()}:e2e/src/canvaDesign.ts', cwd=self.repo),
                         'export const changed = 1;\n')

    def test_keep_spec_discards_the_spec_and_helper_change_when_a_merged_spec_using_it_fails(self):
        self.merge_spec_using_a_helper()
        self.record('LU-001', 'Passed')
        spec = self.write_spec('LU-001', HASHES['LU-001'], imports=['canvaDesign'])
        helper = self.write_helper('canvaDesign.ts')
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                            'specs/link-unfurling/LU-002.spec.ts': 'canva-failed'})

        done = self.keep_spec('LU-001')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('"id": "LU-002"', done.stderr)
        self.assertFalse(spec.exists())
        self.assertEqual(helper.read_text(), 'export const changed = 0;\n')
        self.assertEqual(self.spec_prs(), {})

    def test_keep_spec_replays_nothing_else_for_a_new_helper_file(self):
        self.merge_spec_using_a_helper()
        self.record('LU-001', 'Passed')
        self.write_spec('LU-001', HASHES['LU-001'], imports=['canvaApproval'])
        self.write_helper('canvaApproval.ts')
        self.stub_runs([], {'specs/link-unfurling/LU-001.spec.ts': 'passed'})

        done = self.keep_spec('LU-001')

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['playwright specs/link-unfurling/LU-001.spec.ts'])

    def write_helper(self, name, source='export const changed = 1;\n'):
        helper = self.spec_tree() / 'e2e/src' / name
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text(source)
        return helper

    def repair(self):
        return self.run_party('repair', PARTY, '--tester', 'josh', '--app-version', '1.0.0', '--deploy-sha', 'abc123')

    def merge_specs_using_helpers(self):
        """LU-001 imports canvaDesign directly, LU-002 through canvaFolder; LU-003's spec uses neither."""
        self.merge_specs({
            'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001'], imports=['canvaDesign']),
            'link-unfurling/LU-002.spec.ts': case_spec('LU-002', HASHES['LU-002'], imports=['canvaFolder']),
            '../src/canvaFolder.ts': "import { changed } from './canvaDesign.ts';\n",
            '../src/canvaDesign.ts': 'export const changed = 0;\n'})

    def test_repair_replays_every_spec_using_a_changed_helper_and_keeps_it_when_all_pass(self):
        self.merge_specs_using_helpers()
        self.write_helper('canvaDesign.ts')
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.repair()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['preflight', 'playwright specs/link-unfurling/LU-001.spec.ts',
                                        'playwright specs/link-unfurling/LU-002.spec.ts'])
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        self.assertEqual(git('show', f'origin/{self.spec_branch()}:e2e/src/canvaDesign.ts', cwd=self.repo),
                         'export const changed = 1;\n')
        self.assertIn('helper: `e2e/src/canvaDesign.ts`', self.spec_prs()[self.spec_branch()]['body'])
        self.assertEqual(self.recorded(), [])

    def test_repair_stops_before_replaying_a_change_that_fails_the_e2e_typecheck(self):
        self.merge_specs_using_helpers()
        helper = self.write_helper('canvaDesign.ts')
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'passed'})
        (self.root / 'typecheck-fails').touch()

        done = self.repair()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('npm run --silent typecheck -w e2e failed', done.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(helper.read_text(), 'export const changed = 1;\n')

    def test_repair_replays_only_the_specs_a_changed_helper_reaches(self):
        self.merge_specs_using_helpers()
        self.write_helper('canvaFolder.ts', "import { changed } from './canvaDesign.ts';\nexport const more = 1;\n")
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-002.spec.ts': 'passed'})

        done = self.repair()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['preflight', 'playwright specs/link-unfurling/LU-002.spec.ts'])

    def test_repair_discards_the_change_when_any_spec_using_it_fails(self):
        self.merge_specs_using_helpers()
        helper = self.write_helper('canvaDesign.ts')
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'canva-failed'})

        done = self.repair()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('repair discarded', done.stderr)
        self.assertIn('"id": "LU-002"', done.stderr)
        self.assertEqual(helper.read_text(), 'export const changed = 0;\n')
        self.assertEqual(self.spec_prs(), {})

    def test_repair_keeps_the_change_when_the_only_failures_are_mention_cooldowns(self):
        self.merge_specs_using_helpers()
        self.write_helper('canvaDesign.ts')
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'passed',
                                       'specs/link-unfurling/LU-002.spec.ts': 'cooldown'})

        done = self.repair()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout[:done.stdout.index('Repair kept')])['unverified'], ['LU-002'])
        git('fetch', '--quiet', 'origin', cwd=self.repo)
        self.assertEqual(git('show', f'origin/{self.spec_branch()}:e2e/src/canvaDesign.ts', cwd=self.repo),
                         'export const changed = 1;\n')

    def test_repair_discards_the_change_when_every_spec_using_it_hit_a_cooldown(self):
        self.merge_specs_using_helpers()
        helper = self.write_helper('canvaDesign.ts')
        self.stub_runs([preflight()], {'specs/link-unfurling/LU-001.spec.ts': 'cooldown',
                                       'specs/link-unfurling/LU-002.spec.ts': 'cooldown'})

        done = self.repair()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('no spec using it passed', done.stderr)
        self.assertEqual(helper.read_text(), 'export const changed = 0;\n')

    def test_repair_refuses_a_rewritten_spec_without_an_agent_pass_on_this_build(self):
        self.merge_specs_using_helpers()
        self.write_helper('canvaDesign.ts')
        self.write_spec('LU-001', HASHES['LU-001'])
        self.stub_runs([preflight()], {})

        done = self.repair()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('LU-001 has no agent pass on this build', done.stderr)
        self.assertEqual(self.calls(), [])

    def test_repair_with_no_change_says_so(self):
        self.stub_runs([], {})

        done = self.repair()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('nothing to repair', done.stderr)

    def test_status_counts_automated_cases_per_feature(self):
        self.merge_specs({'link-unfurling/LU-001.spec.ts': case_spec('LU-001', HASHES['LU-001']),
                          'link-unfurling/LU-002.spec.ts': case_spec('LU-002', 'aaaaaaaaaaaa')})

        status = json.loads(self.party('status', PARTY))

        self.assertEqual(status['features'], {'link-unfurling': {'title': 'Link Unfurling', 'untested': 2,
                                                                 'automated': 1}})


class CoreParty(PartyCli):
    """A repo with no Case Specs: a person or agent runs every case, and party.py only keeps the books."""
    CONFIG = {'commit_env': {'HUSKY': '0'}}

    def test_record_keeps_a_human_judged_result(self):
        self.record('LU-001', 'Failed', '--method', 'human', '--note', 'step 2: no card')

        self.assertEqual(self.recorded(), [('LU-001', 'Failed', 'human')])

    def test_show_prints_no_case_spec_marker(self):
        out = self.party('show', 'LU-001')

        self.assertIn('Expected Result:\nA card appears', out)
        self.assertNotIn('caseSpec', out)

    def test_automation_commands_do_not_exist(self):
        for command in ('run', 'keep-spec', 'repair', 'spec-branch'):
            done = self.run_party(command, PARTY)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn('invalid choice', done.stderr)

    def test_status_counts_no_automated_cases(self):
        self.record('LU-001', 'Passed', '--method', 'human')

        status = json.loads(self.party('status', PARTY))

        self.assertEqual(status['features'], {'link-unfurling': {'title': 'Link Unfurling', 'passed': 1,
                                                                 'untested': 1}})

    def test_build_without_a_source_warns_that_results_will_not_name_a_build(self):
        build = json.loads(self.party('build'))

        self.assertEqual((build['app_version'], build['deploy_sha']), ('n/a', 'unknown'))
        self.assertIn('no build source', build['warnings'][0])

    def test_build_reads_the_configured_command(self):
        script = self.root / 'bin' / 'staging-build'
        script.write_text('#!/bin/sh\necho \'{"app_version": "2.3.0", "deploy_sha": "fff000", "warnings": ["slow"]}\'\n')
        script.chmod(0o755)
        self.party_py = self.install_party({'build': {'command': ['staging-build']}})

        build = json.loads(self.party('build'))

        self.assertEqual(build, {'app_version': '2.3.0', 'deploy_sha': 'fff000', 'warnings': ['slow']})

    def test_build_reads_the_latest_successful_github_deploy(self):
        (self.root / 'runs.json').write_text(json.dumps([
            {'headSha': 'bbb222bbb222bbb2', 'headBranch': 'main', 'status': 'in_progress', 'conclusion': '',
             'displayTitle': 'Deploy (staging)'},
            {'headSha': 'ccc333', 'headBranch': 'main', 'status': 'completed', 'conclusion': 'success',
             'displayTitle': 'Deploy (prod)'},
            {'headSha': 'aaa111aaa111aaa1', 'headBranch': 'main', 'status': 'completed', 'conclusion': 'success',
             'displayTitle': 'Deploy (staging)'}]))
        self.party_py = self.install_party(
            {'build': {'github_deploy': {'workflow': 'deploy.yml', 'title_contains': '(staging)'}}})

        build = json.loads(self.party('build'))

        self.assertEqual(build['deploy_sha'], 'aaa111aaa111')
        self.assertIn('in_progress', build['warnings'][0])

    def test_check_counts_the_working_tree_plans_from_a_subdirectory(self):
        done = subprocess.run([sys.executable, str(self.party_py), 'check'], cwd=self.repo / 'docs', env=self.env,
                              capture_output=True, text=True)

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('2 cases', done.stdout)

    def test_check_rejects_a_row_with_a_broken_quote(self):
        (self.repo / self.PLANS_DIR / 'link-unfurling.csv').write_text(PLAN + 'LU-003,Unfurl,"Broken,1. x,y\n')

        done = self.run_party('check')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn('expected 5 columns', done.stderr)


class CustomLayout(PartyCli):
    """A repo whose base branch and plans folder aren't the defaults."""
    BASE = 'develop'
    PLANS_DIR = 'qa/plans'
    CONFIG = {'commit_env': {'HUSKY': '0'}, 'base_branch': 'develop', 'plans_dir': 'qa/plans'}

    def test_record_and_status_read_cases_from_the_configured_base_and_folder(self):
        self.record('LU-001', 'Passed')

        status = json.loads(self.party('status', PARTY))

        self.assertEqual(self.recorded(), [('LU-001', 'Passed', 'agent')])
        self.assertEqual([r['id'] for r in status['remaining']], ['LU-002'])

    def test_sync_merges_the_configured_base(self):
        self.push_to_main('README.md', 'new on develop\n')

        self.assertIn('origin/develop merged into', self.party('sync', PARTY))

    def test_a_new_party_with_no_plans_yet_is_refused_only_for_unknown_features(self):
        done = self.run_party('join', '2026-02-02-other', '--features', 'nope')

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unknown features ['nope']", done.stderr)


# The subclasses keep only their own tests; the ones above them exercise the automation config.
for _subclass in (CoreParty, CustomLayout):
    for _name in [n for n in dir(PartyCli) if n.startswith('test_') and n not in _subclass.__dict__]:
        setattr(_subclass, _name, None)


def case_spec(case_id, case_hash, changes_baseline=False, imports=()):
    options = ', { changesBaseline: true }' if changes_baseline else ''
    helpers = ''.join(f"import {{ changed }} from '../../src/{name}.ts';\n" for name in imports)
    return helpers + textwrap.dedent(f'''\
        import {{ caseSpec, test }} from '../../src/fixtures.ts';

        test(
          '{case_id} does the thing',
          caseSpec('{case_id}', '{case_hash}'{options}),
          async () => {{}}
        );
    ''')


def preflight(ok=True, app_version='1.0.0'):
    failures = [] if ok else [{'check': 'signed-in', 'persona': 'teams-user', 'message': 'signed out of Teams'}]
    return {'ok': ok, 'app_version': app_version, 'failures': failures}


if __name__ == '__main__':
    unittest.main()
