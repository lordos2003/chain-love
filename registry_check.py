#!/usr/bin/env python3
"""Сверка AGENT-REGISTRY.md с живым состоянием git (истина = git).

Запуск (из любой папки, нужен только git и доступ к GitHub):
    python3 registry_check.py                 # реестр берётся с raw.githubusercontent.com
    python3 registry_check.py path/to/AGENT-REGISTRY.md   # локальная копия

Код выхода: 0 = расхождений нет, 1 = есть расхождения (разберись до начала работы).
"""
import os, re, subprocess, sys, urllib.request

FORK = 'https://github.com/lordos2003/chain-love.git'
UPSTREAM = 'https://github.com/Chain-Love/chain-love.git'
RAW = 'https://raw.githubusercontent.com/lordos2003/chain-love/agent-registry/AGENT-REGISTRY.md'
IGNORE = {'main', 'agent-registry'}


def ls(url, *refs):
    out = subprocess.run(['git', 'ls-remote', url, *refs], capture_output=True, text=True, check=True).stdout
    return [l.split('\t') for l in out.splitlines() if l.strip()]


def load_registry(path):
    if path:
        return open(path, encoding='utf-8').read()
    with urllib.request.urlopen(RAW, timeout=30) as r:
        return r.read().decode('utf-8')


text = load_registry(sys.argv[1] if len(sys.argv) > 1 else None)
reg = {}
for line in text.splitlines():
    m = re.match(r'\|\s*`([^`]+)`\s*\|\s*([0-9a-f]{7,40})\s*\|\s*([^|]*)\|\s*([^|]*)\|', line)
    if m:
        reg[m.group(1)] = {'sha': m.group(2), 'pr': m.group(3).strip(), 'state': m.group(4).strip()}

fork = {r[1][len('refs/heads/'):]: r[0] for r in ls(FORK, 'refs/heads/*')}
pull_head = {}                       # sha -> [PR numbers]
for sha, ref in ls(UPSTREAM, 'refs/pull/*/head'):
    pull_head.setdefault(sha, []).append(int(ref.split('/')[2]))
def fetch_open_prs():
    """PR со state=open по API. refs/pull/N/merge остаются и у закрытых PR,
    поэтому «merge-реф есть» != «PR открыт»: gh -> urllib (fallback)."""
    import json as _json
    try:
        out = subprocess.run(['gh', 'api', '--paginate',
                              'repos/Chain-Love/chain-love/pulls?state=open&per_page=100'],
                             capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        out = None
    if out is not None and out.returncode == 0:
        nums = set()
        for line in out.stdout.splitlines():
            if line.strip():
                nums |= {p['number'] for p in _json.loads(line)}
        return nums
    nums = set()
    for page in range(1, 30):
        url = (f'https://api.github.com/repos/Chain-Love/chain-love/pulls'
               f'?state=open&per_page=100&page={page}')
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                                   'User-Agent': 'registry-check'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            batch = _json.load(resp)
        nums |= {p['number'] for p in batch}
        if len(batch) < 100:
            break
    return nums


try:
    open_prs = fetch_open_prs()
except Exception as e:   # API закрыт (например, прокси облачной сессии): запасной вариант
    print(f'ВНИМАНИЕ: API GitHub недоступен ({e}); открытыми считаю PR с merge-рефом. '
          'Это завышает список: merge-реф остаётся и у закрытых PR. Статус спорных PR проверь на странице PR.',
          file=sys.stderr)
    open_prs = {int(ref.split('/')[2]) for _, ref in ls(UPSTREAM, 'refs/pull/*/merge')}
main = ls(UPSTREAM, 'refs/heads/main')[0][0]

# trailer Agent: в голове каждой ветки (коммиты без него помечаются)
cache = os.environ.get('REGISTRY_CACHE') or os.path.expanduser('~/.cache/chainlove-registry')
if not os.path.isdir(os.path.join(cache, '.git')):
    subprocess.run(['git', 'init', '-q', cache], check=True)
subprocess.run(['git', '-C', cache, 'fetch', '-q', '--depth=1', '--filter=tree:0', FORK,
                '+refs/heads/*:refs/fork/*'], capture_output=True)


def agent_of(branch):
    fmt = '%(trailers:key=Agent,valueonly,separator=%x2C)|%(trailers:key=Claude-Session,valueonly,separator=%x2C)'
    r = subprocess.run(['git', '-C', cache, 'log', '-1', f'--format={fmt}', f'refs/fork/{branch}'],
                       capture_output=True, text=True)
    if r.returncode:
        return '?'
    ag, cs = (r.stdout.strip().split('|') + [''])[:2]
    if ag.strip():
        return ag.strip()
    if cs.strip():
        return 'агент не указан (есть Claude-Session ' + cs.strip().rsplit('/', 1)[-1][-8:] + ')'
    return 'агент не указан'


warn = []
drift = []
print(f'upstream main: {main[:7]}   открытых PR в upstream: {len(open_prs)}   веток в форке: {len(fork)}\n')
print(f'{"ветка":32} {"head":8} {"PR (open/closed)":20} {"реестр":28} Agent (head-коммит)')
for b in sorted(fork):
    if b in IGNORE:
        continue
    sha = fork[b]
    prs = sorted(pull_head.get(sha, []))
    prs_s = ','.join(f'#{n}{"" if n in open_prs else "x"}' for n in prs) or '-'
    if b not in reg:
        status = 'НЕТ В РЕЕСТРЕ'
        drift.append(f'ветка {b} ({sha[:7]}) есть в форке, но не в реестре')
    elif not sha.startswith(reg[b]['sha'][:7]):
        status = f'HEAD ИЗМЕНИЛСЯ (в реестре {reg[b]["sha"][:7]})'
        drift.append(f'ветка {b}: head {sha[:7]} != реестр {reg[b]["sha"][:7]}')
    else:
        status = 'ok'
    ag = agent_of(b)
    if ag.startswith('агент не указан') or ag == '?':
        warn.append(f'ветка {b}: в head-коммите нет трейлера Agent:')
    print(f'{b:32} {sha[:7]:8} {prs_s:20} {status:28} {ag}')
for b in sorted(reg):
    if b not in fork:
        drift.append(f'в реестре есть {b}, но ветки в форке нет (удалена/переименована?)')
print('\n(#NNNNx = PR закрыт/слит; без x = открыт)')
if warn:
    print(f'\nПредупреждения (не блокируют): {len(warn)} веток без трейлера Agent: в последнем коммите.')
if drift:
    print('\nРАСХОЖДЕНИЯ (истина = git; обнови реестр или выясни у Олега):')
    for d in drift:
        print(' -', d)
    sys.exit(1)
print('\nРасхождений нет.')
