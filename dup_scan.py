#!/usr/bin/env python3
"""dup_scan.py — какие ячейки уже заняты в открытых PR upstream и в ветках форка.

Истина = git. Скрипт ничего не пушит и не меняет, только читает.

Примеры:
  python3 dup_scan.py --cat apis --col queryPrice --col uptimeSla
  python3 dup_scan.py --cat wallets --col audit --net avalanche --free
  python3 dup_scan.py --cat bridges --col auditsPerformed --slugs debridge,mayan
  python3 dup_scan.py --cat apis --col queryPrice --exclude-branch fill-avalanche-misc --json out.json

Что считается «занято»: ячейка, пустая в upstream main, получает непустое значение
в добавленной строке открытого PR upstream (diff merge-коммита PR) или ветки форка
(diff от общей базы с upstream main). Смотрятся offers/<cat>.csv,
listings/specific-networks/<net>/<cat>.csv и listings/all-networks/<cat>.csv.

--free печатает офферы, где ячейка пуста в main (в оффере и во всех его листингах сетей программы)
и не занята никем, с весом = число сетей программы, ссылающихся на оффер. Офферы с весом 0
(нет листинга ни в одной оплачиваемой сети) по умолчанию скрыты: за них не платят (--all-offers покажет).
Кэш git: $DUP_SCAN_CACHE или ~/.cache/chainlove-dupscan (повторный запуск быстрее).
"""
import argparse, csv, io, json, os, subprocess, sys
from collections import defaultdict

UP = 'https://github.com/Chain-Love/chain-love.git'
FORK = 'https://github.com/lordos2003/chain-love.git'
NETS_FALLBACK = ['algorand', 'avalanche', 'filecoin', 'somnia']
PROGRAM_URL = 'https://raw.githubusercontent.com/lordos2003/chain-love/agent-registry/PROGRAM.json'


def program_nets():
    """Сети программы на сегодня из PROGRAM.json (ветка agent-registry). Сети меняются:
    файл ведёт главный агент со слов Олега; если он старше max_age_days — предупреждение."""
    import datetime, urllib.request
    today = datetime.date.today().isoformat()
    try:
        with urllib.request.urlopen(PROGRAM_URL, timeout=30) as r:
            prog = json.load(r)
    except Exception as e:
        print(f'ВНИМАНИЕ: PROGRAM.json недоступен ({e}); беру запасной список {NETS_FALLBACK}', file=sys.stderr)
        return NETS_FALLBACK
    nets = [n['name'] for n in prog['networks']
            if n.get('from', '0000') <= today and today < n.get('until', '9999')]
    age = (datetime.date.today() - datetime.date.fromisoformat(prog['updated'])).days
    if age > prog.get('max_age_days', 7):
        print(f'ВНИМАНИЕ: PROGRAM.json обновлён {age} дн. назад ({prog["updated"]}). Сети программы могут '
              'измениться: уточни у Олега / в Discussion #41 до начала работы.', file=sys.stderr)
    print(f'сети программы на {today}: {", ".join(nets)} (PROGRAM.json от {prog["updated"]}; '
          f'не подтверждены: {", ".join(prog.get("unconfirmed", {})) or "-"})', file=sys.stderr)
    return nets


NETS = None  # заполняется из PROGRAM.json; --net сужает


def git(repo, *a, check=True):
    r = subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, errors='replace')
    if check and r.returncode:
        raise SystemExit(f'git {" ".join(a[:3])}...: {r.stderr.strip()[:300]}')
    return r.stdout


def ls(url, *refs):
    out = subprocess.run(['git', 'ls-remote', url, *refs], capture_output=True, text=True, check=True).stdout
    return [l.split('\t') for l in out.splitlines() if l.strip()]


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--cat', required=True, help='категория = имя CSV без .csv (apis, wallets, bridges...)')
ap.add_argument('--col', action='append', required=True, help='колонка (можно несколько --col)')
ap.add_argument('--net', action='append', help='сеть (по умолчанию все сети программы)')
ap.add_argument('--slugs', help='только эти slug через запятую')
ap.add_argument('--exclude-pr', action='append', type=int, default=[], help='свой PR, не считать занятым')
ap.add_argument('--exclude-branch', action='append', default=[], help='своя ветка форка, не считать занятой')
ap.add_argument('--no-fork', action='store_true', help='не смотреть ветки форка')
ap.add_argument('--free', action='store_true', help='напечатать свободные ячейки офферов')
ap.add_argument('--json', help='сохранить результат в JSON')
ap.add_argument('--all-offers', action='store_true', help='показывать и офферы без листингов в сетях программы (вес 0, не оплачиваются)')
a = ap.parse_args()
nets = a.net or program_nets()
slugs = set(a.slugs.split(',')) if a.slugs else None

cache = os.environ.get('DUP_SCAN_CACHE') or os.path.expanduser('~/.cache/chainlove-dupscan')
os.makedirs(cache, exist_ok=True)
if not os.path.isdir(os.path.join(cache, '.git')):
    subprocess.run(['git', 'init', '-q', cache], check=True)
    git(cache, 'remote', 'add', 'up', UP)
    git(cache, 'remote', 'add', 'fork', FORK)
    git(cache, 'config', 'remote.up.promisor', 'true')
    git(cache, 'config', 'remote.up.partialclonefilter', 'blob:none')
    git(cache, 'config', 'remote.fork.promisor', 'true')
    git(cache, 'config', 'remote.fork.partialclonefilter', 'blob:none')

off_path = f'references/offers/{a.cat}.csv'
paths = [off_path, f'listings/all-networks/{a.cat}.csv'] + [f'listings/specific-networks/{n}/{a.cat}.csv' for n in nets]

# --- upstream main: заголовки и текущие значения
print('fetch upstream main...', file=sys.stderr)
git(cache, 'fetch', '-q', '--filter=blob:none', '--depth=1', 'up', '+refs/heads/main:refs/up/main')
main_sha = git(cache, 'rev-parse', 'refs/up/main').strip()
hdr, main_val = {}, {}   # path -> header ; (path, slug) -> {col: value}
for p in paths:
    t = git(cache, 'show', f'refs/up/main:{p}', check=False)
    if not t:
        continue
    rr = rows_of(t)
    hdr[p] = rr[0]
    for r in rr[1:]:
        if len(r) == len(rr[0]):
            main_val[(p, r[0])] = dict(zip(rr[0], r))
for c in a.col:
    if c not in hdr.get(off_path, []):
        raise SystemExit(f'колонки {c} нет в {off_path}; есть: {", ".join(hdr.get(off_path, []))}')
paths = [p for p in paths if p in hdr]

taken = defaultdict(list)  # (path, slug, col) -> [(source, value)]


def scan_diff(diff, source):
    cur = None
    for line in diff.split('\n'):
        if line.startswith('+++ '):
            cur = line[6:] if line.startswith('+++ b/') else None
            continue
        if not cur or cur not in hdr or not line.startswith('+') or line.startswith('+++'):
            continue
        try:
            r = next(csv.reader(io.StringIO(line[1:])))
        except Exception:
            continue
        if len(r) != len(hdr[cur]):
            continue
        d = dict(zip(hdr[cur], r))
        s = d[hdr[cur][0]]
        if s == hdr[cur][0]:
            continue                      # строка заголовка в diff
        if slugs and s not in slugs:
            continue
        base = main_val.get((cur, s), {})
        for c in a.col:
            if c in d and d[c].strip() and not base.get(c, '').strip():
                taken[(cur, s, c)].append((source, d[c].strip()))



def open_pr_numbers():
    """Номера реально ОТКРЫТЫХ PR upstream. refs/pull/N/merge остаются и у
    закрытых без слияния PR, поэтому «merge-реф есть» != «PR открыт»: возможны
    закрытые PR, которые считается занявшими ячейки. gh -> urllib (fallback)."""
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
            line = line.strip()
            if line:
                nums |= {p['number'] for p in _json.loads(line)}
        return nums
    try:
        import urllib.request as _ur
        nums = set()
        for page in range(1, 30):
            url = ('https://api.github.com/repos/Chain-Love/chain-love/pulls'
                   '?state=open&per_page=100&page=%d' % page)
            req = _ur.Request(url, headers={'Accept': 'application/vnd.github+json',
                                            'User-Agent': 'dup-scan'})
            with _ur.urlopen(req, timeout=60) as resp:
                batch = _json.load(resp)
            nums |= {p['number'] for p in batch}
            if len(batch) < 100:
                break
        return nums
    except Exception as e:
        print(f'не удалось получить открытые PR ({e}); '
              'считаю открытыми все с merge-рефом', file=sys.stderr)
        return None


# --- открытые PR upstream (merge-реф есть = открыт И сливается; НО он есть и у закрытых
# без слияния — дополнительно сверимся со state=open через gh/API)
merges = {int(ref.split('/')[2]): sha for sha, ref in ls(UP, 'refs/pull/*/merge')}
_open = open_pr_numbers()
if _open is not None:
    merges = {n: s for n, s in merges.items() if n in _open}
prs = sorted(n for n in merges if n not in a.exclude_pr)
print(f'open PR upstream: {len(merges)}; fetch merge-коммитов...', file=sys.stderr)
for i in range(0, len(prs), 100):
    chunk = prs[i:i + 100]
    git(cache, 'fetch', '-q', '--depth=2', '--filter=blob:none', 'up',
        *[f'+refs/pull/{n}/merge:refs/pr/{n}' for n in chunk], check=False)
for n in prs:
    if not git(cache, 'rev-parse', '-q', '--verify', f'refs/pr/{n}', check=False).strip():
        continue
    d = git(cache, 'diff', '-U0', f'refs/pr/{n}^1', f'refs/pr/{n}', '--', *paths, check=False)
    if d:
        scan_diff(d, f'PR #{n}')

# --- ветки форка (в т.ч. без PR)
if not a.no_fork:
    heads = {ref[len('refs/heads/'):]: sha for sha, ref in ls(FORK, 'refs/heads/*')}
    pr_heads = {sha for sha, ref in ls(UP, 'refs/pull/*/head') if int(ref.split('/')[2]) in merges}
    todo = [b for b, s in heads.items() if b not in ('main', 'agent-registry') and b not in a.exclude_branch
            and s not in pr_heads]           # ветки с открытым PR уже учтены через PR
    print(f'ветки форка без открытого PR: {len(todo)}', file=sys.stderr)
    if todo:
        git(cache, 'fetch', '-q', '--filter=blob:none', '--shallow-since=90 days ago', 'up',
            '+refs/heads/main:refs/up/main', check=False)
        git(cache, 'fetch', '-q', '--filter=blob:none', '--shallow-since=90 days ago', 'fork',
            *[f'+refs/heads/{b}:refs/fork/{b}' for b in todo], check=False)
    for b in todo:
        mb = git(cache, 'merge-base', 'refs/up/main', f'refs/fork/{b}', check=False).strip()
        src = f'ветка {b}' + ('' if mb else ' (база не найдена, сравнение с main)')
        d = git(cache, 'diff', '-U0', mb or 'refs/up/main', f'refs/fork/{b}', '--', *paths, check=False)
        if d:
            scan_diff(d, src)

# --- вывод
print(f'\nupstream main {main_sha[:7]}; {a.cat}: {", ".join(a.col)}; сети: {", ".join(nets)}')
if not taken:
    print('Занятых ячеек не найдено.')
for (p, s, c), lst in sorted(taken.items()):
    where = 'offer' if p == off_path else p.split('/')[-2]
    srcs = '; '.join(f'{src}: {v[:50]}' for src, v in lst)
    print(f'ЗАНЯТО  {s:40} {c:24} [{where}]  {srcs}')

# --- вес оффера: сети программы, листинги которых ссылаются на оффер (all-networks = все)
refs = defaultdict(set)            # slug -> {net}
lst_vals = defaultdict(list)       # slug -> [row dict листинга]
for (p, s), d in main_val.items():
    if p == off_path:
        continue
    o = d.get('offer', '')
    if not o.startswith('!offer:'):
        continue
    slug = o[len('!offer:'):]
    net = None if '/all-networks/' in p else p.split('/')[2]
    refs[slug].update(nets if net is None else [net])
    lst_vals[slug].append(d)


def weight(slug):
    return len(refs.get(slug, ()))


free, hidden0 = [], 0
if a.free:
    for (p, s), d in sorted(main_val.items()):
        if p != off_path or (slugs and s not in slugs):
            continue
        w = weight(s)
        for c in a.col:
            if d.get(c, '').strip() or (p, s, c) in taken:
                continue
            if any(l.get(c, '').strip() for l in lst_vals.get(s, [])):
                continue                      # листинг переопределяет ячейку
            if w == 0 and not a.all_offers:
                hidden0 += 1
                continue
            free.append((s, c, w))
    tot = sum(w for _, _, w in free)
    print(f'\nСвободно в офферах (пусто в main и листингах, не занято): {len(free)} ячеек, '
          f'эффективных (× сети программы) {tot}, ≈ {tot * 0.06:.2f} USDC до коэффициента')
    if hidden0:
        print(f'(скрыто {hidden0} ячеек офферов без листингов в сетях программы: вес 0, не оплачиваются; --all-offers покажет)')
    for s, c, w in sorted(free, key=lambda x: (-x[2], x[0])):
        print(f'  {s:40} {c:24} вес {w} ({",".join(sorted(refs.get(s, ()))) or "-"})')

if a.json:
    json.dump({'main': main_sha, 'cat': a.cat, 'cols': a.col, 'nets': nets,
               'taken': [{'file': p, 'slug': s, 'col': c, 'by': [x[0] for x in l], 'values': [x[1] for x in l]}
                         for (p, s, c), l in sorted(taken.items())],
               'free': [{'slug': s, 'col': c, 'weight': w} for s, c, w in free]},
              open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
