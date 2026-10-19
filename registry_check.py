#!/usr/bin/env python3
"""Сверка AGENT-REGISTRY.md с живым состоянием git (истина = git).

Запуск (из любой папки, нужен только git и доступ к GitHub):
    python3 registry_check.py                 # реестр берётся с raw.githubusercontent.com
    python3 registry_check.py path/to/AGENT-REGISTRY.md   # локальная копия

Код выхода: 0 = расхождений нет, 1 = есть расхождения (разберись до начала работы).
"""
import re, subprocess, sys, urllib.request

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
open_prs = {int(ref.split('/')[2]) for _, ref in ls(UPSTREAM, 'refs/pull/*/merge')}
main = ls(UPSTREAM, 'refs/heads/main')[0][0]

drift = []
print(f'upstream main: {main[:7]}   открытых PR в upstream: {len(open_prs)}   веток в форке: {len(fork)}\n')
print(f'{"ветка":32} {"head":8} {"PR (open/closed)":24} реестр')
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
    print(f'{b:32} {sha[:7]:8} {prs_s:24} {status}')
for b in sorted(reg):
    if b not in fork:
        drift.append(f'в реестре есть {b}, но ветки в форке нет (удалена/переименована?)')
print('\n(#NNNNx = PR закрыт/слит; без x = открыт)')
if drift:
    print('\nРАСХОЖДЕНИЯ (истина = git; обнови реестр или выясни у Олега):')
    for d in drift:
        print(' -', d)
    sys.exit(1)
print('\nРасхождений нет.')
