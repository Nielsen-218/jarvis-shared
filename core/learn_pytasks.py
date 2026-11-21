"""The Python-debugging skill's TASKS and JUDGE (code JARVIS's proposals can never edit; its hash is in every episode).

A task is a small function with a spec, a seeded bug (automatic AST mutation) and tests. The judge runs the candidate in the
real Windows AppContainer sandbox (core/project_sandbox.py) and compares ONLY outputs: the expected values come from the
trusted reference on the host, so a candidate can never read the answers. Train and holdout use DIFFERENT functions."""
import ast
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from core import learn, project_sandbox as sb, storage

# name: (spec, correct source, inputs). Edge cases are inside the lists.
TRAIN = {
 "sum_to": ("Return the sum of the integers 1..n inclusive; 0 when n <= 0.",
  "def sum_to(n):\n    total = 0\n    for i in range(1, n + 1):\n        total += i\n    return total\n",
  [(0,), (1,), (4,), (10,), (-3,), (2,), (100,), (7,), (3,), (50,)]),
 "count_greater": ("Return how many elements of xs are strictly greater than t.",
  "def count_greater(xs, t):\n    n = 0\n    for x in xs:\n        if x > t:\n            n += 1\n    return n\n",
  [([], 0), ([1, 2, 3], 2), ([5, 5, 5], 5), ([5, 6, 7], 5), ([-1, -2], -2), ([9], 3), ([1, 2, 3, 4], 0), ([3, 3, 4], 3), ([0], 0), ([10, -10, 10], 9)]),
 "clamp": ("Return x limited to the closed range lo..hi (assume lo <= hi).",
  "def clamp(x, lo, hi):\n    if x < lo:\n        return lo\n    if x > hi:\n        return hi\n    return x\n",
  [(5, 0, 10), (-5, 0, 10), (15, 0, 10), (0, 0, 10), (10, 0, 10), (3, 3, 3), (-1, -5, -2), (7, -3, 4), (1, 2, 9), (100, 1, 99)]),
 "last_index": ("Return the index of the LAST occurrence of v in xs, or -1 if v is absent.",
  "def last_index(xs, v):\n    found = -1\n    for i in range(len(xs)):\n        if xs[i] == v:\n            found = i\n    return found\n",
  [([], 1), ([1, 2, 1], 1), ([1, 2, 3], 3), ([1, 2, 3], 4), ([5], 5), ([7, 7, 7], 7), ([1, 2, 3], 1), ([0, 1, 0, 1], 0), ([2, 4, 6], 6), ([9, 8], 9)]),
 "is_palindrome": ("True when s reads the same forwards and backwards, ignoring case and any non-alphanumeric characters.",
  "def is_palindrome(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]\n",
  [("",), ("a",), ("Aba",), ("ab",), ("A man, a plan, a canal: Panama",), ("race a car",), ("12321",), ("1a2",), (" ",), ("No lemon, no melon",)]),
 "fizz": ("Return 'FizzBuzz' if n is divisible by 15, 'Fizz' if by 3, 'Buzz' if by 5, otherwise str(n).",
  "def fizz(n):\n    if n % 15 == 0:\n        return 'FizzBuzz'\n    if n % 3 == 0:\n        return 'Fizz'\n    if n % 5 == 0:\n        return 'Buzz'\n    return str(n)\n",
  [(1,), (3,), (5,), (15,), (0,), (7,), (30,), (9,), (10,), (14,)]),
 "second_largest": ("Return the second largest DISTINCT value of xs, or None if there are fewer than two distinct values.",
  "def second_largest(xs):\n    vals = sorted(set(xs))\n    if len(vals) < 2:\n        return None\n    return vals[-2]\n",
  [([],), ([1],), ([1, 2],), ([2, 2],), ([3, 1, 2],), ([5, 5, 4],), ([-1, -2, -3],), ([1, 2, 2, 3, 3],), ([9, 9, 9, 8],), ([4, 7],)]),
 "running_max": ("Return the list of prefix maxima of xs (element i is max(xs[:i+1])).",
  "def running_max(xs):\n    out = []\n    best = None\n    for x in xs:\n        if best is None or x > best:\n            best = x\n        out.append(best)\n    return out\n",
  [([],), ([1],), ([1, 3, 2],), ([3, 2, 1],), ([-1, -2],), ([2, 2, 2],), ([1, 2, 3, 4],), ([5, 1, 6],), ([0, -1, 0],), ([4, 9, 9, 2],)]),
 "chunk": ("Split xs into consecutive lists of length k; the last may be shorter. Assume k >= 1.",
  "def chunk(xs, k):\n    out = []\n    for i in range(0, len(xs), k):\n        out.append(xs[i:i + k])\n    return out\n",
  [([], 2), ([1], 2), ([1, 2], 2), ([1, 2, 3], 2), ([1, 2, 3, 4], 2), ([1, 2, 3], 1), ([1, 2, 3], 5), ([1, 2, 3, 4, 5], 3), ([7, 8, 9, 10], 4), ([1, 2, 3, 4, 5, 6, 7], 3)]),
 "count_vowels": ("Return the number of vowels (a, e, i, o, u, either case) in s.",
  "def count_vowels(s):\n    n = 0\n    for c in s:\n        if c.lower() in 'aeiou':\n            n += 1\n    return n\n",
  [("",), ("a",), ("xyz",), ("AEIOU",), ("hello world",), ("rhythm",), ("Education",), ("bcd",), ("UuUu",), ("sky is blue",)]),
 "rotate_left": ("Return xs rotated left by k positions (k may exceed len(xs) or be 0); [] stays [].",
  "def rotate_left(xs, k):\n    if not xs:\n        return []\n    k = k % len(xs)\n    return xs[k:] + xs[:k]\n",
  [([], 3), ([1], 5), ([1, 2, 3], 0), ([1, 2, 3], 1), ([1, 2, 3], 3), ([1, 2, 3], 4), ([1, 2, 3, 4], 2), ([1, 2, 3, 4], 7), ([5, 6], 1), ([1, 2, 3, 4, 5], 6)]),
 "is_sorted": ("True when xs is in non-decreasing order (equal neighbours are fine; empty and single are sorted).",
  "def is_sorted(xs):\n    for i in range(1, len(xs)):\n        if xs[i] < xs[i - 1]:\n            return False\n    return True\n",
  [([],), ([1],), ([1, 2],), ([2, 1],), ([1, 1],), ([1, 2, 2, 3],), ([3, 2, 3],), ([1, 3, 2],), ([-2, -1, 0],), ([5, 5, 4],)]),
 "dedupe": ("Return xs with repeated values removed, keeping the first occurrence of each, in order.",
  "def dedupe(xs):\n    seen = []\n    for x in xs:\n        if x not in seen:\n            seen.append(x)\n    return seen\n",
  [([],), ([1],), ([1, 1],), ([1, 2, 1],), ([3, 2, 1],), ([1, 1, 2, 2, 3, 3],), ([2, 1, 2, 1],), ([0, 0, 0],), ([4, 5, 4, 6, 5],), ([-1, 1, -1],)]),
 "mean_floor": ("Return the integer mean of xs rounded DOWN (floor division by the count); 0 for an empty list.",
  "def mean_floor(xs):\n    if not xs:\n        return 0\n    return sum(xs) // len(xs)\n",
  [([],), ([4],), ([1, 2],), ([1, 2, 3],), ([-1, -2],), ([5, 5, 5],), ([1, 1, 2],), ([0, 0, 1],), ([10, -10],), ([7, 8, 9, 10],)]),
 "longest_run": ("Return the length of the longest run of equal consecutive elements of xs; 0 for an empty list.",
  "def longest_run(xs):\n    best = 0\n    cur = 0\n    prev = None\n    for x in xs:\n        if cur > 0 and x == prev:\n            cur += 1\n        else:\n            cur = 1\n        prev = x\n        if cur > best:\n            best = cur\n    return best\n",
  [([],), ([1],), ([1, 1],), ([1, 2],), ([1, 1, 2, 2, 2],), ([3, 3, 3, 1],), ([1, 2, 1, 2],), ([0, 0, 1, 1],), ([5, 5, 6, 5, 5, 5],), ([2, 2, 2, 2],)]),
 "leap_year": ("True for Gregorian leap years: divisible by 4, except centuries, unless divisible by 400.",
  "def leap_year(y):\n    if y % 400 == 0:\n        return True\n    if y % 100 == 0:\n        return False\n    return y % 4 == 0\n",
  [(2000,), (1900,), (2024,), (2023,), (2100,), (1600,), (4,), (100,), (1996,), (2400,)]),
 "digit_sum": ("Return the sum of the decimal digits of abs(n).",
  "def digit_sum(n):\n    n = abs(n)\n    total = 0\n    while n > 0:\n        total += n % 10\n        n //= 10\n    return total\n",
  [(0,), (5,), (123,), (-123,), (99,), (100,), (1001,), (-7,), (5050,), (987654321,)]),
 "bsearch": ("xs is sorted ascending. Return an index of v in xs, or -1 if absent.",
  "def bsearch(xs, v):\n    lo, hi = 0, len(xs) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if xs[mid] == v:\n            return mid\n        if xs[mid] < v:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1\n",
  [([], 1), ([1], 1), ([1], 2), ([1, 3, 5], 3), ([1, 3, 5], 5), ([1, 3, 5], 1), ([1, 3, 5], 4), ([2, 4, 6, 8], 8), ([2, 4, 6, 8], 2), ([1, 2, 3, 4, 5, 6, 7], 6)]),
}

HOLDOUT = {
 "min_gap": ("Return the smallest absolute difference between ADJACENT elements of xs; 0 when len(xs) < 2.",
  "def min_gap(xs):\n    if len(xs) < 2:\n        return 0\n    best = abs(xs[1] - xs[0])\n    for i in range(2, len(xs)):\n        d = abs(xs[i] - xs[i - 1])\n        if d < best:\n            best = d\n    return best\n",
  [([],), ([4],), ([1, 3],), ([5, 1, 2],), ([1, 2, 10],), ([3, 3],), ([10, 0, 9, 1],), ([-1, 1, 0],), ([7, 2, 8, 8],), ([1, 100, 51, 52],)]),
 "count_between": ("Return how many elements of xs satisfy lo <= x <= hi (both bounds inclusive).",
  "def count_between(xs, lo, hi):\n    n = 0\n    for x in xs:\n        if x >= lo and x <= hi:\n            n += 1\n    return n\n",
  [([], 0, 5), ([1, 2, 3], 1, 3), ([1, 2, 3], 2, 2), ([0, 5, 10], 1, 9), ([5, 5], 5, 5), ([-3, -2, -1], -2, 0), ([1, 2, 3, 4], 5, 9), ([1, 2, 3, 4], 0, 9), ([4, 6], 4, 5), ([9], 9, 9)]),
 "first_negative": ("Return the index of the first negative element of xs, or -1 if there is none.",
  "def first_negative(xs):\n    for i, x in enumerate(xs):\n        if x < 0:\n            return i\n    return -1\n",
  [([],), ([1, 2],), ([-1],), ([1, -1],), ([0, -2, -3],), ([0],), ([5, 6, -7],), ([-5, 6],), ([0, 0, 0],), ([3, -1, -1],)]),
 "title_words": ("Capitalise the first letter of each space-separated word and lower-case the rest; keep the spacing as it is.",
  "def title_words(s):\n    return ' '.join(w[:1].upper() + w[1:].lower() for w in s.split(' '))\n",
  [("",), ("a",), ("hello world",), ("HELLO",), ("two  spaces",), ("x y z",), ("mIxEd cAsE",), (" lead",), ("trail ",), ("one",)]),
 "trim_zeros": ("Return xs without LEADING and TRAILING zeros (zeros in the middle stay).",
  "def trim_zeros(xs):\n    lo, hi = 0, len(xs)\n    while lo < hi and xs[lo] == 0:\n        lo += 1\n    while hi > lo and xs[hi - 1] == 0:\n        hi -= 1\n    return xs[lo:hi]\n",
  [([],), ([0],), ([1],), ([0, 1, 0],), ([0, 0, 1, 2, 0, 0],), ([1, 0, 2],), ([0, 0],), ([5, 0],), ([0, 5],), ([0, 1, 0, 2, 0],)]),
 "pair_count": ("Return how many pairs of positions i < j have xs[i] + xs[j] == t.",
  "def pair_count(xs, t):\n    n = 0\n    for i in range(len(xs)):\n        for j in range(i + 1, len(xs)):\n            if xs[i] + xs[j] == t:\n                n += 1\n    return n\n",
  [([], 0), ([1], 1), ([1, 2], 3), ([1, 2, 3], 4), ([2, 2, 2], 4), ([1, 1], 3), ([0, 0, 0, 0], 0), ([1, 5, 3, 3], 6), ([-1, 1, 0], 0), ([4, 6, 5, 5], 10)]),
 "anagram": ("True when a and b contain the same letters ignoring case and spaces.",
  "def anagram(a, b):\n    x = sorted(c.lower() for c in a if c != ' ')\n    y = sorted(c.lower() for c in b if c != ' ')\n    return x == y\n",
  [("", ""), ("a", "a"), ("ab", "ba"), ("ab", "bb"), ("Listen", "Silent"), ("a b", "ba"), ("abc", "ab"), ("Dormitory", "dirty room"), ("aab", "abb"), ("x", "X")]),
 "fib": ("Return the n-th Fibonacci number with fib(0) = 0 and fib(1) = 1.",
  "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n",
  [(0,), (1,), (2,), (3,), (5,), (8,), (10,), (15,), (20,), (4,)]),
 "max_subarray": ("Return the largest sum of a NON-EMPTY contiguous slice of xs; 0 for an empty list.",
  "def max_subarray(xs):\n    if not xs:\n        return 0\n    best = cur = xs[0]\n    for x in xs[1:]:\n        cur = max(x, cur + x)\n        best = max(best, cur)\n    return best\n",
  [([],), ([5],), ([-3],), ([1, 2, 3],), ([-1, -2],), ([2, -1, 2],), ([-2, 1, -3, 4, -1, 2, 1, -5, 4],), ([1, -5, 1],), ([3, -1, -1, 3],), ([0, 0],)]),
 "diagonal_sum": ("m is a square matrix (list of rows). Return the sum of its main diagonal.",
  "def diagonal_sum(m):\n    total = 0\n    for i in range(len(m)):\n        total += m[i][i]\n    return total\n",
  [([],), ([[5]],), ([[1, 2], [3, 4]],), ([[1, 0, 0], [0, 2, 0], [0, 0, 3]],), ([[9, 9], [9, 9]],), ([[-1, 2], [3, -4]],), ([[1, 2, 3], [4, 5, 6], [7, 8, 9]],), ([[0]],), ([[2, 7], [1, 1]],), ([[1, 1, 1], [1, 1, 1], [1, 1, 5]],)]),
 "rle": ("Run-length encode s as each character followed by its run length: 'aaabb' -> 'a3b2'; '' -> ''.",
  "def rle(s):\n    out = []\n    i = 0\n    while i < len(s):\n        j = i\n        while j < len(s) and s[j] == s[i]:\n            j += 1\n        out.append(s[i] + str(j - i))\n        i = j\n    return ''.join(out)\n",
  [("",), ("a",), ("aa",), ("ab",), ("aaabb",), ("abab",), ("zzzzzzzzzzzz",), ("aabcccd",), ("xyyz",), ("qqq r",)]),
 "days_in_month": ("Return the number of days in month m (1-12) of year y, Gregorian rules (February has 29 days in leap years).",
  "def days_in_month(m, y):\n    if m == 2:\n        leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0\n        return 29 if leap else 28\n    if m in (4, 6, 9, 11):\n        return 30\n    return 31\n",
  [(1, 2023), (2, 2023), (2, 2024), (2, 1900), (2, 2000), (4, 2023), (6, 2023), (9, 2023), (11, 2023), (12, 2023)]),
 "take_positive": ("Return the leading run of strictly positive elements of xs (stop at the first element <= 0).",
  "def take_positive(xs):\n    out = []\n    for x in xs:\n        if x <= 0:\n            break\n        out.append(x)\n    return out\n",
  [([],), ([1],), ([0],), ([1, 2, 0, 3],), ([-1, 2],), ([3, 2, 1],), ([5, -1, 5],), ([0, 1],), ([2, 2, 2, 0],), ([1, 0, 0],)]),
 "merge_sorted": ("a and b are sorted ascending. Return one sorted list with all their elements (duplicates kept).",
  "def merge_sorted(a, b):\n    out = []\n    i = j = 0\n    while i < len(a) and j < len(b):\n        if a[i] <= b[j]:\n            out.append(a[i])\n            i += 1\n        else:\n            out.append(b[j])\n            j += 1\n    return out + a[i:] + b[j:]\n",
  [([], []), ([1], []), ([], [2]), ([1, 3], [2, 4]), ([1, 2], [1, 2]), ([5, 6], [1, 2]), ([1, 5, 9], [2]), ([2], [1, 5, 9]), ([1, 1], [1]), ([-3, 0], [-2, 4])]),
 "gcd_list": ("Return the greatest common divisor of all numbers in xs (non-negative ints); 0 for an empty list.",
  "def gcd_list(xs):\n    g = 0\n    for x in xs:\n        a, b = g, x\n        while b:\n            a, b = b, a % b\n        g = a\n    return g\n",
  [([],), ([7],), ([4, 6],), ([12, 18, 24],), ([5, 7],), ([0, 5],), ([0, 0],), ([100, 75],), ([9, 27, 81],), ([8, 12, 20],)]),
 "ordinal": ("Return n with its English ordinal suffix: 1st, 2nd, 3rd, 4th, 11th, 12th, 13th, 21st, 101st, 111th (n >= 1).",
  "def ordinal(n):\n    if 10 <= n % 100 <= 20:\n        suffix = 'th'\n    else:\n        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')\n    return str(n) + suffix\n",
  [(1,), (2,), (3,), (4,), (11,), (12,), (13,), (21,), (101,), (111,)]),
}

STEP_LIMIT = 200000

# ------------------------------------------------------------------ bugs: seeded by AST mutation
_CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_ARITH = {ast.Add: ast.Sub, ast.Sub: ast.Add}


class _Nth(ast.NodeTransformer):
    """Apply operator `kind` to the k-th applicable node only."""
    def __init__(self, kind, k):
        self.kind, self.k, self.i, self.done = kind, k, 0, False

    def _hit(self):
        hit = self.i == self.k
        self.i += 1
        if hit:
            self.done = True
        return hit

    def visit_Compare(self, n):
        self.generic_visit(n)
        if self.kind == "cmp" and type(n.ops[0]) in _CMP and self._hit():
            n.ops[0] = _CMP[type(n.ops[0])]()
        return n

    def visit_Constant(self, n):
        if self.kind == "const" and type(n.value) is int and self._hit():
            return ast.copy_location(ast.Constant(n.value + 1), n)
        return n

    def visit_BinOp(self, n):
        self.generic_visit(n)
        if self.kind == "arith" and type(n.op) in _ARITH and self._hit():
            n.op = _ARITH[type(n.op)]()
        return n

    def visit_BoolOp(self, n):
        self.generic_visit(n)
        if self.kind == "bool" and self._hit():
            n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
        return n

    def visit_If(self, n):
        self.generic_visit(n)
        if self.kind == "dropif" and self._hit():
            return n.orelse or ast.copy_location(ast.Pass(), n)        # the guard disappears
        return n

    def visit_Call(self, n):
        self.generic_visit(n)
        if self.kind == "range" and isinstance(n.func, ast.Name) and n.func.id == "range" and n.args and self._hit():
            n.args[-1] = ast.BinOp(n.args[-1], ast.Sub(), ast.Constant(1))      # off-by-one at the end of a range
        return n


SEED_KINDS = ("cmp", "const", "arith", "bool", "dropif", "range")     # bug kinds that are seeded
SEARCH_KINDS = ("cmp", "arith", "bool")                               # what the generic repair search may undo (plus constants +-1)


def mutate(source, kind, k):
    t = _Nth(kind, k)
    tree = t.visit(ast.parse(source))
    if not t.done:
        return None
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


def mutations(source, kinds=SEED_KINDS, limit=12):
    out = []
    for kind in kinds:
        for k in range(limit):
            m = mutate(source, kind, k)
            if m is None:
                break
            if m != source:
                out.append((kind, k, m))
    return out


def const_shifts(source):
    """Every integer constant moved by +1 and by -1 (the search may undo a bug in either direction)."""
    out = []
    n = sum(1 for x in ast.walk(ast.parse(source)) if isinstance(x, ast.Constant) and type(x.value) is int)
    for k in range(n):
        for d in (1, -1):
            tree = ast.parse(source)
            [x for x in ast.walk(tree) if isinstance(x, ast.Constant) and type(x.value) is int][k].value += d
            out.append(ast.unparse(tree) + "\n")
    return out


# ------------------------------------------------------------------ the sandbox judge
_WORKER = r'''
import io, json, sys
SRCS = json.loads(%(srcs)s)
FUNC = %(func)r
INPUTS = json.loads(%(inputs)s)
LIMIT = %(limit)d
class Spin(Exception): pass
def make_tracer():
    box = [0]
    def local(frame, event, arg):
        box[0] += 1
        if box[0] > LIMIT: raise Spin()
        return local
    def glob(frame, event, arg):
        return local
    return glob
res = []
real = sys.stdout
for src in SRCS:
    ns = {}
    try:
        sys.stdout = io.StringIO()
        exec(compile(src, "<candidate>", "exec"), ns)
        fn = ns[FUNC]
    except BaseException as e:
        sys.stdout = real
        res.append({"load_error": type(e).__name__}); continue
    outs = []
    for a in INPUTS:
        try:
            sys.stdout = io.StringIO()
            sys.settrace(make_tracer())
            try: v = fn(*json.loads(json.dumps(a)))
            finally: sys.settrace(None)
            outs.append({"ok": json.loads(json.dumps(v, default=str))})
        except BaseException as e:
            outs.append({"err": type(e).__name__})
    sys.stdout = real
    res.append(outs)
print("@@RESULT@@" + json.dumps(res))
'''


def _lab():
    root = Path(os.environ.get("JARVIS_LEARN_LAB") or (Path(storage.DATA) / "dojo" / "python" / "lab"))
    if not (root / ".jarvis-project-lab").is_file():
        root.parent.mkdir(parents=True, exist_ok=True)
        sb.initialize(root)
    for attempt in range(6):                                     # Windows briefly refuses while another sandbox user deletes a profile
        try:
            sb.prepare_runtime(root / "runtime")
            sb.seal_profile(root)
            return root
        except sb.SandboxUnavailable:
            if attempt == 5:
                raise
            time.sleep(2 + attempt * 2)
    return root


def run_candidates(sources, func, inputs, timeout=30, cancel=None):
    """-> one entry per source: {load_error} or [ {ok: value} | {err: Name} per input ], run for real inside the AppContainer."""
    lab = _lab()
    ws = lab / ("ws-" + uuid.uuid4().hex[:8])
    ws.mkdir()
    (ws / "w.py").write_text(_WORKER % {"srcs": repr(json.dumps(list(sources))), "func": func,
                                        "inputs": repr(json.dumps([list(i) for i in inputs])), "limit": STEP_LIMIT}, encoding="utf-8")
    sb.grant_readonly(ws, lab)
    try:
        r = sb.run(lab / "runtime", ws, ws / "w.py", timeout=timeout, cancel=cancel)
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    line = next((x for x in r.get("stdout", "").splitlines() if x.startswith("@@RESULT@@")), None)
    if not r.get("passed_boundary") or r.get("exit_code") != 0 or line is None:
        return [{"load_error": "sandbox:" + str(r.get("error") or r.get("exit_code"))} for _ in sources]
    return json.loads(line[len("@@RESULT@@"):])


def expected(func, correct_src, inputs):
    ns = {}
    exec(compile(correct_src, "<ref>", "exec"), ns)
    return [json.loads(json.dumps(ns[func](*json.loads(json.dumps(list(a)))), default=str)) for a in inputs]


def compare(outs, want):
    """-> bool per input (a load error fails everything)."""
    if isinstance(outs, dict):
        return [False] * len(want)
    return [("ok" in o and o["ok"] == w) for o, w in zip(outs, want)]


def judge(task, source, cancel=None):
    """The immutable verdict for a candidate source on a task (hidden tests = every input)."""
    want = expected(task["func"], task["correct"], task["inputs"])
    ok = compare(run_candidates([source], task["func"], task["inputs"], cancel=cancel)[0], want)
    vis = all(ok[i] for i in task["visible"])
    fails = len(ok) - sum(ok)
    return {"full": fails == 0, "stages_ok": int(any(ok)) + int(vis) + int(fails == 0),
            "fail_count": fails, "total": len(ok), "failing": [i for i, v in enumerate(ok) if not v]}


# ------------------------------------------------------------------ the task bank
def _build(split, funcs, per_kind=2, per_func=4):
    out = []
    for name, (spec, correct, inputs) in funcs.items():
        want = expected(name, correct, inputs)
        muts = mutations(correct)
        muts.sort(key=lambda m: learn.digest([name, m[0], m[1]]))
        results = run_candidates([m[2] for m in muts], name, inputs) if muts else []
        picked, kinds = [], {}
        for (kind, k, src), outs in zip(muts, results):
            if isinstance(outs, dict):
                continue                                       # does not even load
            ok = compare(outs, want)
            if all(ok) or kinds.get(kind, 0) >= per_kind:
                continue                                       # equivalent mutant, or enough of this kind
            first_fail = next(i for i, v in enumerate(ok) if not v)
            visible = sorted({0, 1, first_fail})              # the visible tests always reveal the bug
            kinds[kind] = kinds.get(kind, 0) + 1
            picked.append({"id": f"py:{name}:{kind}{k}", "func": name, "kind_seeded": kind, "spec": spec, "correct": correct, "buggy": src,
                           "inputs": [list(i) for i in inputs], "visible": visible, "split": split, "label": f"{name} ({kind})",
                           "digest": learn.digest([spec, src, inputs, want]),
                           "visible_fail": sum(1 for i in visible if not ok[i]),
                           "visible_err": sum(1 for i in visible if "err" in outs[i])})
            if len(picked) >= per_func:
                break
        out.extend(picked)
    return out


_CACHE = {}


def tasks(split):
    if split not in _CACHE:
        _CACHE[split] = _build(split, TRAIN if split == "train" else HOLDOUT)
    return _CACHE[split]


def judge_files():
    return [Path(__file__), Path(sb.__file__)]
