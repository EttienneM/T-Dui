#!/usr/bin/env python3
"""Test Party bookkeeping — the only writer of results. See the test parties README named in test-party.json."""
import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name('test-party.json')
DEFAULTS = {
    'remote': 'origin',
    'base_branch': 'main',
    'plans_dir': 'docs/test-plans',
    'parties_dir': 'docs/test-parties',
    'party_branch_prefix': 'test-party/',
    # Extra environment for party commits, e.g. {"HUSKY": "0"} to skip a pre-commit hook that can't run in a worktree.
    'commit_env': {},
    # Where `build` reads what the environment under test is running. Either or both of:
    #   "github_deploy": {"workflow": "deploy.yml", "title_contains": "(staging)"}
    #   "command": ["./scripts/staging-build"]  — prints JSON {"app_version"?, "deploy_sha"?, "warnings"?}
    'build': {},
    # Case Spec replays. null for parties a person or agent runs by hand. See automation defaults below.
    'automation': None,
}
AUTOMATION_DEFAULTS = {
    'e2e_dir': 'e2e',
    'specs_dir': 'e2e/specs',
    'helpers_dir': 'e2e/src',
    'spec_branch_prefix': 'test/case-specs/',
    # Paths under the repo root a spec worktree links to, since it has neither installed packages nor local profiles.
    'shared_paths': ['node_modules'],
    'typecheck': None,
    # Prints one JSON object to stdout: {"ok": bool, "app_version"?: str, "failures": [{"check", "persona", "message"}]}
    'preflight': None,
    # Run with cwd at e2e_dir; must print Playwright's JSON reporter output. {spec} and {case_id} are substituted.
    'runner': ['npx', 'playwright', 'test', '{spec}', '--reporter=json', '--output=test-results/{case_id}'],
    # Helper files that drive a third-party system during setup, as a filename regex. A failure in one is kind `setup`.
    'setup_helpers': None,
    'setup_label': 'setup',
    # Error regexes for refusals that judge nothing (rate limits, cooldowns). A failure matching one is `transient`.
    'transient_errors': [],
    'transient_label': 'a transient refusal',
    # Relative imports between helper and spec files, for finding which specs a helper change reaches.
    'import_pattern': r"""from\s+'(\.{1,2}/[^']+\.ts)'""",
}


def load_config():
    config = {**DEFAULTS, **(json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {})}
    if config['automation'] is not None:
        config['automation'] = {**AUTOMATION_DEFAULTS, **config['automation']}
    return config


CONFIG = load_config()
REMOTE = CONFIG['remote']
BASE_BRANCH = CONFIG['base_branch']
BASE_REF = f'{REMOTE}/{BASE_BRANCH}'
CASES_REF = os.environ.get('TEST_PARTY_CASES_REF', BASE_REF)
PLANS_DIR = CONFIG['plans_dir']
PARTIES_DIR = CONFIG['parties_dir']
BRANCH_PREFIX = CONFIG['party_branch_prefix']
AUTO = CONFIG['automation']
if AUTO:
    E2E_DIR = AUTO['e2e_dir'].rstrip('/')
    SPECS_DIR = AUTO['specs_dir'].rstrip('/')
    HELPERS_DIR = AUTO['helpers_dir'].rstrip('/')
    SPEC_BRANCH_PREFIX = AUTO['spec_branch_prefix']
    SETUP_HELPER = (re.compile(rf'(?:^|/){re.escape(HELPERS_DIR)}/({AUTO["setup_helpers"]})$')
                    if AUTO['setup_helpers'] else None)
    TRANSIENT = [re.compile(p) for p in AUTO['transient_errors']]
    IMPORT = re.compile(AUTO['import_pattern'])
RESULT_FIELDS = ['case_id', 'result', 'case_hash', 'app_version', 'deploy_sha', 'timestamp', 'note', 'method']
RESULTS = ['Passed', 'Failed', 'Blocked']
METHODS = ['agent', 'human', 'spec']
# On a timestamp tie the more alarming result wins, so a failure is never hidden.
TIE_RANK = {'Passed': 0, 'Blocked': 1, 'Failed': 2}


class PartyError(Exception):
    pass


def git(*args, cwd=None, env=None):
    done = subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode:
        raise PartyError(f'git {" ".join(args)} failed: {done.stderr.strip()}')
    return done.stdout


def repo_root():
    return Path(git('rev-parse', '--show-toplevel').strip())


def commit(*args, cwd):
    git('commit', '--quiet', *args, cwd=cwd, env={**os.environ, **CONFIG['commit_env']})


def run_configured(command, cwd, **substitutions):
    argv = [part.format(**substitutions) for part in command]
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        raise PartyError(f'{argv[0]} is not installed (from test-party.json: {" ".join(command)})')


def typecheck_e2e(tree):
    """Run before a spec or helper change is replayed, since a type error would only show up as a confusing failure."""
    if not AUTO['typecheck']:
        return
    done = run_configured(AUTO['typecheck'], tree)
    if done.returncode:
        raise PartyError(f'{" ".join(AUTO["typecheck"])} failed in {tree}:\n{(done.stdout + done.stderr).strip()}')


def plan_files(ref):
    if ref is None:
        plans = repo_root() / PLANS_DIR
        return {p.name: p.read_text() for p in sorted(plans.glob('*.csv'))}
    try:
        names = git('ls-tree', '--full-tree', '--name-only', f'{ref}:{PLANS_DIR}').split()
    except PartyError:
        return {}
    return {n: git('show', f'{ref}:{PLANS_DIR}/{n}') for n in names if n.endswith('.csv')}


def load_cases(ref=CASES_REF):
    cases = {}
    for name, text in plan_files(ref).items():
        feature = name[:-4]
        reader = csv.reader(io.StringIO(text))
        title = next(reader)[0]
        next(reader)
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != 5:
                raise PartyError(f'{PLANS_DIR}/{name}:{reader.line_num}: expected 5 columns, got {len(row)}')
            case_id, scenario, action, steps, expected = row
            if case_id in cases:
                raise PartyError(f'{case_id} appears in both {cases[case_id]["feature"]}.csv and {name}')
            cases[case_id] = {'feature': feature, 'title': title, 'scenario': scenario,
                              'action': action, 'steps': steps, 'expected': expected,
                              'hash': case_hash(steps, expected)}
    return cases


def case_hash(steps, expected):
    lines = [' '.join(line.split()) for cell in (steps, expected) for line in cell.splitlines()]
    return hashlib.sha256('\n'.join(l for l in lines if l).encode()).hexdigest()[:12]


CASE_SPEC = re.compile(r"""caseSpec\(\s*['"]([^'"]+)['"]\s*,\s*['"]([0-9a-f]+)['"]\s*(,\s*\{[^}]*\bchangesBaseline:\s*true)?""")


def is_spec_file(path):
    return re.search(r'\.(spec|test)\.[cm]?[jt]sx?$', str(path)) is not None


def load_case_specs(ref=CASES_REF):
    """Case Specs on `ref` by Case ID; a spec that isn't merged there doesn't exist yet."""
    if not AUTO:
        return {}
    paths = [p for p in git('ls-tree', '--full-tree', '-r', '--name-only', ref, '--', SPECS_DIR).split()
             if is_spec_file(p)]
    return case_specs_in((path, git('show', f'{ref}:{path}')) for path in paths)


def case_specs_in(sources):
    specs = {}
    for path, source in sources:
        for case_id, spec_hash, changes_baseline in CASE_SPEC.findall(source):
            if case_id in specs:
                raise PartyError(f'{case_id} has Case Specs in both {specs[case_id]["file"]} and {path}')
            specs[case_id] = {'file': path[len(E2E_DIR) + 1:], 'hash': spec_hash,
                              'changes_baseline': bool(changes_baseline)}
    return specs


def spec_sources(tree):
    return ((str(p.relative_to(tree)), p.read_text()) for p in (tree / SPECS_DIR).rglob('*') if is_spec_file(p))


def is_automated(case, spec):
    return spec is not None and spec['hash'] == case['hash']


def worktrees_dir():
    return Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip()) / 'test-party-worktrees'


def worktree_for(party):
    return worktrees_dir() / party


def party_dir(party):
    return worktree_for(party) / PARTIES_DIR / party


def open_parties():
    git('fetch', '--quiet', REMOTE)
    refs = git('for-each-ref', '--format=%(refname:short)', f'refs/remotes/{REMOTE}/{BRANCH_PREFIX}').split()
    return [r.split(BRANCH_PREFIX, 1)[1] for r in refs]


def join(party, features):
    tree = worktree_for(party)
    branch = BRANCH_PREFIX + party
    if party in open_parties():
        if not tree.exists():
            git('worktree', 'add', '--quiet', '-B', branch, str(tree), f'{REMOTE}/{branch}')
        git('pull', '--quiet', '--rebase', cwd=tree)
        return tree
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-[a-z0-9-]+', party):
        raise PartyError('new party names are <yyyy-mm-dd>-<kebab-name>')
    scope = validate_scope(features)
    git('worktree', 'add', '--quiet', '-b', branch, str(tree), CASES_REF)
    folder = tree / PARTIES_DIR / party
    folder.mkdir(parents=True)
    (folder / 'party.md').write_text(
        f'# {party}\n\nFeatures: {", ".join(scope) if scope else "all"}\n')
    git('add', '.', cwd=folder)
    commit('-m', f'docs: start test party {party}', cwd=tree)
    git('push', '--quiet', '-u', REMOTE, branch, cwd=tree)
    return tree


def validate_scope(features, cases=None):
    known = {c['feature'] for c in (cases or load_cases()).values()}
    unknown = sorted(set(features) - known)
    if unknown:
        raise PartyError(f'unknown features {unknown}; known: {sorted(known)}')
    return features


def read_scope(party):
    manifest = (party_dir(party) / 'party.md').read_text()
    line = re.search(r'^Features:\s*(.*)$', manifest, re.M)
    features = [f.strip() for f in line.group(1).split(',')] if line else []
    return [] if features in ([], [''], ['all']) else features


def latest_results(party):
    latest = {}
    for results in sorted(party_dir(party).glob('*.csv')):
        with results.open(newline='') as f:
            for line in csv.DictReader(f):
                key = (line['timestamp'], TIE_RANK.get(line['result'], 0))
                prior = latest.get(line['case_id'])
                if prior is None or key >= prior['_key']:
                    latest[line['case_id']] = {**line, 'tester': results.stem, '_key': key}
    return latest


def state_of(case, result, build):
    if result is None:
        return 'untested'
    if result['case_hash'] != case['hash']:
        return 'stale'
    if result['result'] == 'Failed':
        failed_build = (result['app_version'], result['deploy_sha'])
        return 'failed-retest' if build and build != failed_build else 'failed'
    return result['result'].lower()


REMAINING = {'untested', 'stale', 'failed-retest'}


def github_deploy_build(source):
    try:
        runs = json.loads(subprocess.run(
            ['gh', 'run', 'list', '--workflow', source['workflow'], '--limit', '30',
             '--json', 'headSha,headBranch,status,conclusion,displayTitle'],
            check=True, capture_output=True, text=True).stdout)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {'deploy_sha': 'unknown', 'warnings': ['gh unavailable — deploy_sha is unknown']}
    title = source.get('title_contains')
    deploys = [r for r in runs if not title or title in r['displayTitle']]
    branch = source.get('branch', BASE_BRANCH)
    warnings = []
    if deploys and deploys[0]['status'] != 'completed':
        warnings.append(f'a deploy of {deploys[0]["headBranch"]} is {deploys[0]["status"]} — '
                        'the build will change mid-session; pin the build again after it lands')
    done = next((r for r in deploys if r['conclusion'] == 'success'), None)
    if done is None:
        return {'deploy_sha': 'unknown', 'warnings': warnings + ['no successful deploy found']}
    if done['headBranch'] != branch:
        warnings.append(f'the environment is running branch {done["headBranch"]}, not {branch} — '
                        f'cases describe {branch}, so expect differences')
    return {'deploy_sha': done['headSha'][:12], 'branch': done['headBranch'], 'warnings': warnings}


def command_build(command):
    done = run_configured(command, repo_root())
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError:
        return {'warnings': [f'{" ".join(command)} exited {done.returncode} without JSON: {done.stderr.strip()}']}
    return report


def current_build():
    """What the environment under test runs: the app version and the deploy SHA, either of which can change alone."""
    build = {'app_version': 'n/a', 'deploy_sha': 'unknown', 'warnings': []}
    sources = CONFIG['build']
    found = []
    if sources.get('github_deploy'):
        found.append(github_deploy_build(sources['github_deploy']))
    if sources.get('command'):
        found.append(command_build(sources['command']))
    if not found:
        build['warnings'].append('no build source in test-party.json — results will not say what they ran against')
    for report in found:
        build['warnings'] += report.get('warnings', [])
        build.update({k: v for k, v in report.items() if k != 'warnings'})
    return build


def cmd_parties(args):
    print('\n'.join(open_parties()) or 'no open parties')


def cmd_join(args):
    tree = join(args.party, [f.strip() for f in args.features.split(',')] if args.features else [])
    print(f'party {args.party} at {tree}')


def cmd_build(args):
    print(json.dumps(current_build(), indent=2))


def cases_in_scope(party, cases, feature=None):
    """The party's scope, and its cases narrowed to `feature` if given."""
    scope = validate_scope(read_scope(party), cases)
    if feature:
        validate_scope([feature], cases)
    return scope, {case_id: case for case_id, case in cases.items()
                   if (not scope or case['feature'] in scope) and (not feature or case['feature'] == feature)}


def spec_branch_refs(party):
    git('fetch', '--quiet', '--prune', REMOTE)
    return git('for-each-ref', '--format=%(refname:short)',
               f'refs/remotes/{REMOTE}/{SPEC_BRANCH_PREFIX}{party}/').split()


def unspecced(party, in_scope, latest):
    """Cases whose latest result is a pass and that have no current Case Spec on main or a spec branch."""
    specced = set()
    for ref in [CASES_REF, *spec_branch_refs(party)]:
        specced |= {case_id for case_id, spec in load_case_specs(ref).items()
                    if case_id in in_scope and is_automated(in_scope[case_id], spec)}
    return [{'id': case_id, 'scenario': case['scenario'], 'action': case['action']}
            for case_id, case in in_scope.items()
            if case_id not in specced and state_of(case, latest.get(case_id), None) == 'passed']


def party_status(party, build=None, feature=None, with_unspecced=False):
    cases = load_cases()
    scope, in_scope = cases_in_scope(party, cases, feature)
    latest = latest_results(party)
    specs = load_case_specs()
    summary, remaining = {}, []
    for case_id, case in in_scope.items():
        result = latest.get(case_id)
        state = state_of(case, result, build)
        counts = summary.setdefault(case['feature'], {'title': case['title']})
        counts[state] = counts.get(state, 0) + 1
        if is_automated(case, specs.get(case_id)):
            counts['automated'] = counts.get('automated', 0) + 1
        if state in REMAINING:
            remaining.append({'id': case_id, 'state': state, 'scenario': case['scenario'],
                              'action': case['action'], 'last': result and
                              f'{result["result"]} by {result["tester"]}: {result["note"]}'})
    status = {'scope': scope or 'all', 'features': summary, 'remaining': remaining,
              'results_for_unknown_ids': sorted(set(latest) - set(cases))}
    if with_unspecced:
        status['unspecced'] = unspecced(party, in_scope, latest)
    return status


def cmd_status(args):
    joined_worktree(args.party)
    build = (args.app_version, args.deploy_sha) if args.app_version and args.deploy_sha else None
    print(json.dumps(party_status(args.party, build, args.feature, getattr(args, 'unspecced', False)), indent=2))


STATUS_START = '<!-- test-party-status:start -->'
STATUS_END = '<!-- test-party-status:end -->'
STATUS_COLUMNS = [('Passed', ['passed']), ('Failed', ['failed', 'failed-retest']),
                  ('Blocked', ['blocked']), ('Stale', ['stale']), ('Untested', ['untested'])]


def status_section(party):
    summary = party_status(party)['features']
    newest = max(latest_results(party).values(), key=lambda r: r['_key'], default=None)
    lines = [STATUS_START, '## Current status', '']
    if newest:
        lines += [f'Last result {newest["timestamp"]}, on app {newest["app_version"]} · '
                  f'deploy {newest["deploy_sha"]}. `party.py` keeps this section current.', '']
    lines += ['| Feature | ' + ' | '.join(name for name, _ in STATUS_COLUMNS) + ' |',
              '| --- |' + ' --- |' * len(STATUS_COLUMNS)]
    for counts in summary.values():
        cells = [str(sum(counts.get(s, 0) for s in states) or '–') for _, states in STATUS_COLUMNS]
        lines.append(f'| {counts["title"]} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines + [STATUS_END])


def with_status(body, section):
    if STATUS_START in body and STATUS_END in body:
        before, rest = body.split(STATUS_START, 1)
        return before + section + rest.split(STATUS_END, 1)[1]
    heading = re.search(r'^## ', body, re.M)
    if heading:
        return body[:heading.start()] + section + '\n\n' + body[heading.start():]
    return body.rstrip() + '\n\n' + section + '\n'


def gh(*args):
    try:
        done = subprocess.run(['gh', *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise PartyError('gh is not installed')
    if done.returncode:
        raise PartyError(f'gh {args[0]} {args[1]} failed: {done.stderr.strip()}')
    return done.stdout


def refresh_pr(party):
    try:
        pr = json.loads(gh('pr', 'view', BRANCH_PREFIX + party, '--json', 'number,state,body'))
        if pr['state'] != 'OPEN':
            return f'PR description not updated: PR #{pr["number"]} is {pr["state"].lower()}'
        body = with_status(pr['body'], status_section(party))
        if body == pr['body']:
            return f'PR #{pr["number"]} description already current'
        gh('pr', 'edit', str(pr['number']), '--body', body)
        return f'PR #{pr["number"]} description updated'
    except PartyError as error:
        return f'PR description not updated: {error}'


def cmd_pr(args):
    joined_worktree(args.party)
    print(refresh_pr(args.party))


def cmd_sync(args):
    tree = joined_worktree(args.party)
    branch = BRANCH_PREFIX + args.party
    git('fetch', '--quiet', REMOTE, cwd=tree)
    if is_ancestor(BASE_REF, 'HEAD', tree):
        print(f'{branch} already has everything on {BASE_REF}')
        return
    try:
        git('merge', '--quiet', '--no-edit', BASE_REF, cwd=tree)
    except PartyError:
        conflicted = git('diff', '--name-only', '--diff-filter=U', cwd=tree).split()
        if not conflicted:
            raise
        git('merge', '--abort', cwd=tree)
        raise PartyError(f'merging {BASE_REF} into {branch} would conflict in {", ".join(conflicted)}, '
                         'so nothing was merged — resolve it by hand in the party worktree')
    try:
        git('push', '--quiet', cwd=tree)
    except PartyError:
        # An unpushed merge would be flattened by the next record's pull --rebase.
        git('reset', '--quiet', '--hard', 'ORIG_HEAD', cwd=tree)
        raise PartyError('push failed, so the merge was undone — run sync again')
    print(f'{BASE_REF} merged into {branch} and pushed')


def is_ancestor(older, newer, tree):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer], cwd=tree).returncode == 0


def cmd_show(args):
    case = load_cases().get(args.case_id)
    if case is None:
        raise PartyError(f'no case {args.case_id} on {CASES_REF}')
    print(f'{args.case_id} · {case["title"]} · {case["scenario"]}\n{case["action"]}\n\n'
          f'Steps:\n{case["steps"]}\n\nExpected Result:\n{case["expected"]}'
          + (f"\n\ncaseSpec('{args.case_id}', '{case['hash']}')" if AUTO else ''))


def joined_worktree(party):
    tree = worktree_for(party)
    if not tree.exists():
        raise PartyError(f'not joined to {party} — run join first')
    git('pull', '--quiet', '--rebase', cwd=tree)
    return tree


def add_method_column(results):
    with results.open(newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == RESULT_FIELDS:
            return
        # Rows from before the method column were all judged by the agent.
        earlier = [{'method': 'agent', **row} for row in reader]
    with results.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(earlier)


def append_results(party, tester, rows, app_version, deploy_sha, method):
    results = party_dir(party) / f'{tester}.csv'
    is_new = not results.exists()
    if not is_new:
        add_method_column(results)
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    with results.open('a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow({**row, 'app_version': app_version, 'deploy_sha': deploy_sha,
                             'timestamp': timestamp, 'note': ' '.join(row['note'].split()),
                             'method': method})
    return results


def commit_and_push(tree, path, message):
    git('add', str(path), cwd=tree)
    try:
        commit('-m', message, cwd=tree)
    except PartyError:
        # Put the log back as it was, so a rerun appends its rows once rather than twice.
        git('reset', '--quiet', '--', str(path), cwd=tree)
        if git('ls-files', '--', str(path), cwd=tree).strip():
            git('checkout', '--', str(path), cwd=tree)
        else:
            Path(path).unlink()
        raise
    for _ in range(3):
        try:
            git('push', '--quiet', cwd=tree)
            return
        except PartyError:
            git('pull', '--quiet', '--rebase', cwd=tree)
    raise PartyError('recorded and committed locally, but push kept failing — push the worktree by hand')


def cmd_record(args):
    case = load_cases().get(args.case_id)
    if case is None:
        raise PartyError(f'no case {args.case_id} on {CASES_REF}')
    tree = joined_worktree(args.party)
    row = {'case_id': args.case_id, 'result': args.result, 'case_hash': case['hash'], 'note': args.note}
    results = append_results(args.party, args.tester, [row], args.app_version, args.deploy_sha, args.method)
    commit_and_push(tree, results, f'docs: record {args.case_id} {args.result.lower()} in test party {args.party}')
    print(f'{args.case_id} {args.result} recorded and pushed')
    print(refresh_pr(args.party))


def import_results(party, tester, rows, app_version, deploy_sha, method, cases=None):
    """Appends many results in one commit, or none if any is for an unknown case or differs from main."""
    cases = cases or load_cases()
    problems = []
    for line, row in enumerate(rows, start=2):
        case = cases.get(row['case_id'])
        if case is None:
            problems.append(f'line {line}: no case {row["case_id"]} on {CASES_REF}')
        elif row['result'] not in RESULTS:
            problems.append(f'line {line}: result must be one of {RESULTS}')
        elif row['case_hash'] != case['hash']:
            problems.append(f'line {line}: {row["case_id"]} text differs from {CASES_REF}')
    if problems:
        raise PartyError('nothing imported:\n' + '\n'.join(problems))
    tree = joined_worktree(party)
    rows = [{'case_id': r['case_id'], 'result': r['result'], 'case_hash': r['case_hash'],
             'note': r.get('note') or ''} for r in rows]
    results = append_results(party, tester, rows, app_version, deploy_sha, method)
    commit_and_push(tree, results, f'docs: import {len(rows)} results for {tester} into test party {party}')
    return f'{len(rows)} results for {tester} imported and pushed\n{refresh_pr(party)}'


def cmd_import(args):
    with open(args.file, newline='') as f:
        rows = list(csv.DictReader(f))
    print(import_results(args.party, args.tester, rows, args.app_version, args.deploy_sha, args.method))


def preflight():
    if not AUTO['preflight']:
        return {'ok': True, 'failures': []}
    # Its progress goes to stderr, which the tester sees; stdout is one JSON report.
    argv = AUTO['preflight']
    try:
        done = subprocess.run(argv, cwd=repo_root(), stdout=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise PartyError(f'{argv[0]} is not installed (preflight in test-party.json)')
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        raise PartyError(f'preflight could not run (exit {done.returncode}); see its output above')


def check_preflight(app_version):
    report = preflight()
    problems = [f'{f["persona"]} {f["check"]}: {f["message"]}' for f in report['failures']]
    if not problems and report.get('app_version') and report['app_version'] != app_version:
        problems.append(f'the environment runs app {report["app_version"]}, not {app_version} — pin the build again')
    if problems:
        raise PartyError('preflight failed:\n' + '\n'.join(problems))


def differs_from_main(spec):
    """Specs run from the working copy, so one edited locally or not yet pulled isn't the merged spec."""
    return bool(git('diff', '--name-only', CASES_REF, '--', f'{E2E_DIR}/{spec["file"]}').strip())


ANSI = re.compile(r'\x1b\[[0-9;]*m')


def reported_results(suite):
    for spec in suite.get('specs', []):
        for test in spec['tests']:
            yield from test['results']
    for child in suite.get('suites', []):
        yield from reported_results(child)


def failure_summary(message):
    """The error's first line, plus the locator it was waiting for, which Playwright puts in the call log."""
    lines = [line.strip(' -') for line in ANSI.sub('', message).splitlines() if line.strip()]
    waiting = next((line for line in lines[1:] if line.startswith('waiting for ')), None)
    return lines[0] + (f' {waiting[0].upper()}{waiting[1:]}' if waiting else '')


def failure_kind(errors):
    """`transient` when a refusal that judges nothing stopped it: replay it later. `setup` when the first error is in
    a setup helper: the spec broke driving a third-party system before it judged the product, so its case needs a
    helper repair, not a rerun, and the helper is named as `file:line`. Otherwise `product`."""
    if any(p.search(e['message']) for p in TRANSIENT for e in errors):
        return {'kind': 'transient'}
    location = next((e['location'] for e in errors if e.get('location')), None)
    helper = SETUP_HELPER and location and SETUP_HELPER.search(location['file'].replace(os.sep, '/'))
    if not helper:
        return {'kind': 'product'}
    helpers_subdir = os.path.relpath(HELPERS_DIR, E2E_DIR)
    return {'kind': 'setup', 'helper': f'{helpers_subdir}/{helper.group(1)}:{location["line"]}'}


def run_spec(case_id, spec, e2e_dir=None):
    """None if the spec passed, otherwise why it failed, its `kind`, and where the runner left screenshots and
    traces."""
    e2e_dir = e2e_dir or repo_root() / E2E_DIR
    # Playwright empties its output folder at the start of a run, so a shared one would lose earlier failures' traces.
    done = run_configured(AUTO['runner'], e2e_dir, spec=spec['file'], case_id=case_id)
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError:
        first_line = next((l for l in done.stderr.splitlines() if l.strip()), '')
        return {'error': f'spec runner exited {done.returncode} without a report: {first_line}', 'kind': 'product',
                'artifacts': None}
    stats = report['stats']
    if done.returncode == 0 and stats['expected'] > 0 and not stats['unexpected'] and not stats['flaky']:
        return None
    results = [r for suite in report['suites'] for r in reported_results(suite)]
    errors = [e for r in results for e in r.get('errors', []) if e.get('message')]
    errors += [e for e in report.get('errors', []) if e.get('message')]
    paths = [a['path'] for r in results for a in r.get('attachments', []) if a.get('path')]
    return {'error': failure_summary(errors[0]['message']) if errors else f'no test passed ({stats})',
            **failure_kind(errors),
            'artifacts': os.path.relpath(os.path.dirname(paths[0])) if paths else None}


def cmd_run(args):
    joined_worktree(args.party)
    check_preflight(args.app_version)
    cases = load_cases()
    specs = load_case_specs()
    _, in_scope = cases_in_scope(args.party, cases, args.feature)
    with_spec = [c for c in in_scope if c in specs]
    stale = [c for c in with_spec if not is_automated(cases[c], specs[c])]
    edited_locally = [c for c in with_spec if c not in stale and differs_from_main(specs[c])]
    automated = [(c, specs[c]) for c in with_spec if c not in stale and c not in edited_locally]
    passed, failed, not_run, stop_reason = [], [], [], None
    for i, (case_id, spec) in enumerate(automated):
        print(f'running {case_id}', file=sys.stderr)
        failure = run_spec(case_id, spec)
        if failure:
            failed.append({'id': case_id, **failure})
        else:
            passed.append(case_id)
        if spec['changes_baseline']:
            try:
                check_preflight(args.app_version)
            except PartyError as error:
                # A broken restore would fail every later spec for the wrong reason.
                stop_reason = f'baseline not restored after {case_id}: {error}'
                not_run = [c for c, _ in automated[i + 1:]]
                break
    if passed:
        rows = [{'case_id': c, 'result': 'Passed', 'case_hash': specs[c]['hash']} for c in passed]
        print(import_results(args.party, args.tester, rows, args.app_version, args.deploy_sha, 'spec', cases),
              file=sys.stderr)
    print(json.dumps({'passed': passed, 'failed': failed, 'not_run': not_run, 'stale': stale,
                      'differs_from_main': edited_locally}, indent=2))
    if stop_reason:
        raise PartyError(stop_reason)


def spec_session(party, tester):
    """Today's Case Spec branch for `tester` in `party`, and the worktree it's checked out in."""
    session = f'{tester}-{datetime.date.today().isoformat()}'
    return SPEC_BRANCH_PREFIX + f'{party}/{session}', worktrees_dir() / 'case-specs' / party / session


def spec_worktree(party, tester):
    """Creates the session's spec worktree off main on first use, so specs never land on the party branch."""
    branch, tree = spec_session(party, tester)
    if tree.exists():
        return branch, tree
    git('fetch', '--quiet', REMOTE)
    if git('ls-remote', '--heads', REMOTE, branch).strip():
        git('worktree', 'add', '--quiet', '-B', branch, str(tree), f'{REMOTE}/{branch}')
    else:
        git('worktree', 'add', '--quiet', '--no-track', '-b', branch, str(tree), CASES_REF)
    top = repo_root()
    for shared in AUTO['shared_paths']:
        if (top / shared).exists() and not (tree / shared).exists():
            (tree / shared).parent.mkdir(parents=True, exist_ok=True)
            (tree / shared).symlink_to(top / shared)
    return branch, tree


def check_agent_pass(party, case_id, case, build):
    """A spec encodes what the agent saw, so it is written only from an agent pass of the current case text."""
    result = latest_results(party).get(case_id)
    method = result and (result.get('method') or 'agent')
    if (result is None or result['result'] != 'Passed' or method != 'agent'
            or result['case_hash'] != case['hash'] or (result['app_version'], result['deploy_sha']) != build):
        last = f'{result["result"]} by {method}' if result else 'untested'
        raise PartyError(f'{case_id} has no agent pass on this build to write a Case Spec from (latest: {last})')


def spec_in_tree(tree, case_id, case):
    folder = f'{SPECS_DIR}/{case["feature"]}/'
    spec = case_specs_in(spec_sources(tree)).get(case_id)
    if spec is None or not f'{E2E_DIR}/{spec["file"]}'.startswith(folder) \
            or not Path(spec['file']).name.startswith(f'{case_id}.'):
        raise PartyError(f'no caseSpec({case_id!r}, ...) in {tree / folder}{case_id}.spec.*')
    if spec['hash'] != case['hash']:
        raise PartyError(f'{case_id} spec is marked {spec["hash"]}, but the case on {CASES_REF} is {case["hash"]}')
    return spec


def discard(tree):
    git('reset', '--quiet', '--hard', cwd=tree)
    excludes = [arg for shared in AUTO['shared_paths'] for arg in ('-e', f'/{shared}')]
    git('clean', '-fdq', *excludes, cwd=tree)
    return ''


def set_aside(tree, files, case_id):
    """Reverts only `files`, so specs still waiting for their own keep-spec stay in the worktree, and saves them as a
    patch that `git apply` restores. Returns a note naming the patch."""
    files = sorted(files)
    patch = worktrees_dir() / 'set-aside' / tree.relative_to(worktrees_dir() / 'case-specs') / f'{case_id}.patch'
    patch.parent.mkdir(parents=True, exist_ok=True)
    git('add', '--', *files, cwd=tree)
    patch.write_text(git('diff', '--cached', '--binary', '--', *files, cwd=tree))
    in_head = git('ls-tree', '-r', '--name-only', 'HEAD', '--', *files, cwd=tree).split()
    git('reset', '--quiet', '--', *files, cwd=tree)
    if in_head:
        git('checkout', '--quiet', 'HEAD', '--', *in_head, cwd=tree)
    for file in set(files) - set(in_head):
        (tree / file).unlink()
    return f' (saved as {patch}; `git apply` it in the worktree to restore)'


def spec_pr_body(party, tree):
    changed = git('diff', '--name-only', f'{CASES_REF}...HEAD', '--', SPECS_DIR, cwd=tree).split()
    on_main = set(git('ls-tree', '-r', '--name-only', CASES_REF, '--', SPECS_DIR, cwd=tree).split())
    lines = [f'- {Path(path).name.split(".")[0]} ({"rewritten" if path in on_main else "new"}): `{path}`'
             for path in changed]
    helpers = git('diff', '--name-only', f'{CASES_REF}...HEAD', '--', HELPERS_DIR, cwd=tree).split()
    lines += [f'- helper: `{path}`' for path in helpers]
    return '\n'.join([
        f'Case Specs the `/test-party` agent wrote in test party `{party}`. Each was written from an agent '
        '`Passed` run and kept only after it passed a solo replay; each helper change was kept only after every '
        f'spec using it passed one, except specs its commit names as unverified ({AUTO["transient_label"]}). '
        'Party runs ignore them until this merges.', '', *lines, ''])


def open_spec_pr(party, branch, tree):
    body = spec_pr_body(party, tree)
    try:
        pr = json.loads(gh('pr', 'view', branch, '--json', 'number,state,url'))
    except PartyError:
        pr = None
    if pr and pr['state'] == 'OPEN':
        gh('pr', 'edit', str(pr['number']), '--body', body)
        return pr['url']
    title = f'test: case specs from test party {party} ({branch.rsplit("/", 1)[1]})'
    return gh('pr', 'create', '--base', BASE_BRANCH, '--head', branch, '--title', title, '--body', body).strip()


def cmd_keep_spec(args):
    cases = load_cases()
    case = cases.get(args.case_id)
    if case is None:
        raise PartyError(f'no case {args.case_id} on {CASES_REF}')
    joined_worktree(args.party)
    check_agent_pass(args.party, args.case_id, case, (args.app_version, args.deploy_sha))
    branch, tree = spec_worktree(args.party, args.tester)
    spec = spec_in_tree(tree, args.case_id, case)
    spec_file = f'{E2E_DIR}/{spec["file"]}'
    pending = uncommitted_e2e_changes(tree)
    if spec_file not in pending:
        raise PartyError(f'{args.case_id} spec has no uncommitted change in {tree}, so there is nothing to keep')
    own = ({spec_file} | imported_by(tree, spec_file)) & pending
    undo = lambda: set_aside(tree, own, args.case_id)  # noqa: E731
    typecheck_e2e(tree)
    failure = run_spec(args.case_id, spec, tree / E2E_DIR)
    if spec['changes_baseline']:
        try:
            check_preflight(args.app_version)
        except PartyError as error:
            note = undo()
            raise PartyError(f'baseline not restored after {args.case_id}, so its spec was discarded: {error}{note}')
    if failure:
        note = undo()
        raise PartyError(f'{args.case_id} spec failed its solo replay, so it was discarded: {failure["error"]}'
                         + (f' (in {AUTO["setup_label"]} helper {failure["helper"]})'
                            if failure['kind'] == 'setup' else '')
                         + (f' (read {failure["artifacts"]}/error-context.md first; its screenshot and trace are there too)'
                            if failure['artifacts'] else '')
                         + note)
    # A helper the spec changed is held to repair's bar: every committed spec using it must pass too. Specs still
    # waiting in the worktree are judged by their own keep-spec.
    helpers = {f for f in own if f.startswith(f'{HELPERS_DIR}/')}
    others = [(case_id, other) for case_id, other in specs_using(tree, helpers)
              if case_id != args.case_id and f'{E2E_DIR}/{other["file"]}' not in pending
              and case_id in cases and is_automated(cases[case_id], other)]
    failures, unverified = replay_each(tree, others, args.app_version, f'the {args.case_id} spec', undo)
    if failures:
        note = undo()
        raise PartyError(f'{args.case_id} spec and its helper change discarded{note}; every spec using the helper '
                         'must pass:\n' + json.dumps(failures, indent=2))
    rewritten = bool(git('ls-tree', '--name-only', CASES_REF, '--', spec_file, cwd=tree).strip())
    git('add', '--', *sorted(own), cwd=tree)
    commit('-m',
           f'test: {"rewrite" if rewritten else "add"} case spec {args.case_id} from test party {args.party}'
           + unverified_note(unverified), '--', *sorted(own), cwd=tree)
    git('push', '--quiet', '-u', REMOTE, branch, cwd=tree)
    if unverified:
        print(f'unverified ({AUTO["transient_label"]}): {", ".join(unverified)}')
    print(f'{args.case_id} Case Spec kept on {branch}')
    print(f'PR: {open_spec_pr(args.party, branch, tree)}')


def uncommitted_e2e_changes(tree):
    lines = git('status', '--porcelain', '--untracked-files=all', '--', HELPERS_DIR, SPECS_DIR, cwd=tree).splitlines()
    return {line[3:].split(' -> ')[-1] for line in lines}


def import_graph(tree):
    """Each helper and spec file, with the local files it imports."""
    imports = {}
    for path in [*(tree / HELPERS_DIR).rglob('*'), *(tree / SPECS_DIR).rglob('*')]:
        if not path.is_file():
            continue
        file = path.relative_to(tree).as_posix()
        try:
            source = path.read_text()
        except UnicodeDecodeError:
            continue
        imports[file] = {os.path.normpath(os.path.join(os.path.dirname(file), m)) for m in IMPORT.findall(source)}
    return imports


def imported_by(tree, file):
    """Every local file `file` imports, directly or through other helpers."""
    imports = import_graph(tree)
    found, todo = set(), [file]
    while todo:
        for dep in imports.get(todo.pop(), ()):
            if dep not in found:
                found.add(dep)
                todo.append(dep)
    return found


def spec_files_using(tree, changed):
    """Spec files that are in `changed` or import one of them, directly or through other helpers."""
    imports = import_graph(tree)
    affected = set(changed)
    while True:
        more = {file for file, deps in imports.items() if deps & affected} - affected
        if not more:
            return sorted(f for f in affected if is_spec_file(f) and (tree / f).exists())
        affected |= more


def specs_using(tree, changed):
    """(case ID, spec) for each Case Spec in the worktree that is in `changed` or imports one of them."""
    by_file = {f'{E2E_DIR}/{spec["file"]}': (case_id, spec)
               for case_id, spec in case_specs_in(spec_sources(tree)).items()}
    return [by_file[f] for f in spec_files_using(tree, changed) if f in by_file]


def replay_each(tree, replay, app_version, discarded, undo):
    """Replays each spec on its own. Returns the failures, and the case IDs a transient refusal left unjudged.
    A broken baseline runs `undo` at once, discarding the change named `discarded`."""
    failures, unverified = [], []
    for case_id, spec in replay:
        print(f'replaying {case_id}', file=sys.stderr)
        failure = run_spec(case_id, spec, tree / E2E_DIR)
        if failure and failure['kind'] == 'transient':
            unverified.append(case_id)
        elif failure:
            failures.append({'id': case_id, **failure})
        if spec['changes_baseline']:
            try:
                check_preflight(app_version)
            except PartyError as error:
                note = undo()
                raise PartyError(f'baseline not restored after {case_id}, so {discarded} was discarded: {error}{note}')
    return failures, unverified


def unverified_note(unverified):
    return f'\n\nUnverified ({AUTO["transient_label"]}): {", ".join(unverified)}' if unverified else ''


def cmd_repair(args):
    cases = load_cases()
    joined_worktree(args.party)
    branch, tree = spec_worktree(args.party, args.tester)
    changed = uncommitted_e2e_changes(tree)
    if not changed:
        raise PartyError(f'nothing to repair: no uncommitted change under {HELPERS_DIR} or {SPECS_DIR} in {tree}')
    using = specs_using(tree, changed)
    build = (args.app_version, args.deploy_sha)
    replay, stale = [], []
    for case_id, spec in using:
        case = cases.get(case_id)
        rewritten = f'{E2E_DIR}/{spec["file"]}' in changed
        if rewritten:
            if case is None:
                raise PartyError(f'no case {case_id} on {CASES_REF}')
            spec_in_tree(tree, case_id, case)
            check_agent_pass(args.party, case_id, case, build)
        if case and is_automated(case, spec):
            replay.append((case_id, spec))
        else:
            stale.append(case_id)
    if not replay:
        raise PartyError('no current Case Spec uses the change, so there is nothing to replay it against')
    typecheck_e2e(tree)
    check_preflight(args.app_version)
    failures, unverified = replay_each(tree, replay, args.app_version, 'the repair', lambda: discard(tree))
    if failures:
        discard(tree)
        raise PartyError('repair discarded; every spec using it must pass:\n' + json.dumps(failures, indent=2))
    if len(unverified) == len(replay):
        discard(tree)
        raise PartyError(f'repair discarded: no spec using it passed; {AUTO["transient_label"]} stopped '
                         f'{", ".join(unverified)}, so rerun repair later')
    ids = [case_id for case_id, _ in replay]
    git('add', '-A', '--', '.', *[f':(exclude){shared}' for shared in AUTO['shared_paths']], cwd=tree)
    commit('-m', f'test: repair case specs {", ".join(ids)} from test party {args.party}'
           + unverified_note(unverified), cwd=tree)
    git('push', '--quiet', '-u', REMOTE, branch, cwd=tree)
    print(json.dumps({'replayed': ids, 'unverified': unverified, 'stale': stale}, indent=2))
    print(f'Repair kept on {branch}')
    print(f'PR: {open_spec_pr(args.party, branch, tree)}')


def cmd_spec_branch(args):
    joined_worktree(args.party)
    _, tree = spec_worktree(args.party, args.tester)
    print(tree)


def cmd_check(args):
    cases = load_cases(ref=None)
    print(f'{len(cases)} cases, IDs unique, 5 columns each')


def cmd_config(args):
    print(json.dumps(CONFIG, indent=2))


def add_build_args(parser):
    parser.add_argument('--app-version', required=True, help="from `build` (or preflight); 'n/a' if there is none")
    parser.add_argument('--deploy-sha', required=True, help='from `build`')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('parties', help='list open parties').set_defaults(run=cmd_parties)
    p = sub.add_parser('join', help='join a party, starting it if it does not exist')
    p.add_argument('party')
    p.add_argument('--features', help='comma-separated slugs for a new party; default all')
    p.set_defaults(run=cmd_join)
    sub.add_parser('build', help='the build the environment under test runs').set_defaults(run=cmd_build)
    p = sub.add_parser('status', help='counts and remaining cases')
    p.add_argument('party')
    p.add_argument('--feature')
    p.add_argument('--app-version')
    p.add_argument('--deploy-sha')
    if AUTO:
        p.add_argument('--unspecced', action='store_true',
                       help='also list Passed cases with no current Case Spec on main or a spec branch')
    p.set_defaults(run=cmd_status)
    p = sub.add_parser('pr', help="refresh the status section of the party's PR description",
                       description='record and import do this after every push; run it after a case fix '
                                   'merges to the base branch, since that can make results stale.')
    p.add_argument('party')
    p.set_defaults(run=cmd_pr)
    p = sub.add_parser('sync', help='merge the base branch into the party branch and push',
                       description='The branch is shared by every tester, so the base is merged in, never rebased.')
    p.add_argument('party')
    p.set_defaults(run=cmd_sync)
    p = sub.add_parser('show', help='print a case from the base branch')
    p.add_argument('case_id')
    p.set_defaults(run=cmd_show)
    p = sub.add_parser('record', help='append a result, commit, and push')
    p.add_argument('party')
    p.add_argument('case_id')
    p.add_argument('result', choices=RESULTS)
    p.add_argument('--tester', required=True)
    add_build_args(p)
    p.add_argument('--note', default='')
    p.add_argument('--method', choices=METHODS, default='agent',
                   help='how the case was judged: agent drove it, a human ran it, or a spec replayed it')
    p.set_defaults(run=cmd_record)
    p = sub.add_parser('import', help='append many results from a CSV in one commit, rejecting stale or unknown cases',
                       description='FILE has columns case_id, result, case_hash, and optionally note. '
                                   'Every line gets the same build and timestamp.')
    p.add_argument('party')
    p.add_argument('tester')
    p.add_argument('file')
    add_build_args(p)
    p.add_argument('--method', choices=METHODS, default='agent', help='how every case in FILE was judged')
    p.set_defaults(run=cmd_import)
    if AUTO:
        p = sub.add_parser('run', help="replay the party's Automated Cases and record their passes",
                           description='Runs preflight, then every Automated Case in scope, one at a time. Passes '
                                       'are recorded with method spec. Failing specs record nothing; each is printed '
                                       'with its error, the folder holding its screenshot and trace, and its kind: '
                                       'setup when it broke in a setup helper (named as helper), so repair it; '
                                       'transient when a refusal that judges nothing stopped it, so leave it; '
                                       'product when it broke judging the product, so rerun its case.')
        p.add_argument('party')
        p.add_argument('--feature')
        p.add_argument('--tester', required=True)
        add_build_args(p)
        p.set_defaults(run=cmd_run)
        p = sub.add_parser('spec-branch', help="print the worktree of this session's Case Spec branch, creating it",
                           description='The branch is off the base, one per tester per day, and holds only Case '
                                       'Specs and the helpers they need. Write specs in this worktree, then keep each '
                                       'with keep-spec.')
        p.add_argument('party')
        p.add_argument('--tester', required=True)
        p.set_defaults(run=cmd_spec_branch)
        p = sub.add_parser('keep-spec', help="solo-replay a Case Spec from an agent pass and add it to the session's PR",
                           description=f"The spec must be at {SPECS_DIR}/<feature>/<case-id>.spec.* in spec-branch's "
                                       "worktree, marked with the case's current hash, and the case's latest result "
                                       'an agent pass on this build. A changed helper also replays every current spec '
                                       'using it, as repair does. A failing replay sets the spec and its changed '
                                       'helpers aside as a patch.')
        p.add_argument('party')
        p.add_argument('case_id')
        p.add_argument('--tester', required=True)
        add_build_args(p)
        p.set_defaults(run=cmd_keep_spec)
        p = sub.add_parser('repair', help='replay every Case Spec a helper change touches, and keep it if all pass',
                           description=f'For a spec run reports with kind setup. Edit {HELPERS_DIR} helpers, and any '
                                       "spec that needs it, in spec-branch's worktree. repair replays every current "
                                       'spec that imports a changed file, directly or through another helper, each on '
                                       "its own. If all pass it commits the change to the session's PR; otherwise it "
                                       'discards every uncommitted change in the worktree and prints each failure. A '
                                       'spec stopped by a transient refusal is unverified, not failed, while another '
                                       "spec passes. A changed spec needs what keep-spec needs: the case's current "
                                       'hash and an agent pass on this build.')
        p.add_argument('party')
        p.add_argument('--tester', required=True)
        add_build_args(p)
        p.set_defaults(run=cmd_repair)
    sub.add_parser('check', help='validate the test plans in the working tree').set_defaults(run=cmd_check)
    sub.add_parser('config', help='print the effective test-party.json').set_defaults(run=cmd_config)
    args = parser.parse_args()
    try:
        args.run(args)
    except PartyError as error:
        sys.exit(f'error: {error}')


if __name__ == '__main__':
    main()
