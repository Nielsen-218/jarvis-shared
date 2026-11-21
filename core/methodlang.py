"""The language a SELF-WRITTEN PC method is allowed to be written in, and the only things it can touch.

A learned method is plain Python text written by the local model (core/acquire.py), but it is never trusted as Python:
  * check() parses it and refuses anything outside a small whitelist: one `def run(ui, params):`, assignments, if, `for` over
    finite things, comprehensions, string/list/dict helpers. No import, while, lambda, class, nested def, global, try, with,
    dunder or underscore names, attribute access other than the whitelisted helper names. So every method terminates.
  * run() executes it with an empty builtins table (a few pure helpers only) and ONE capability object, `ui`, whose primitives
    are UI Automation reads plus Invoke/Select: no keyboard, mouse, files, network, processes or shell. Every primitive call is
    counted against a budget and a wall-clock deadline.
The same `ui` object drives the isolated lab desktop during practice and the owner's desktop in production, so what was
practised is exactly what runs. Production use is still gated by owner review (core/acquire.approved).
"""
import ast
import time

MAX_CALLS = 400
MAX_SECONDS = 25.0
MAX_RANGE = 200
MAX_SOURCE = 6000

_NODES = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign, ast.AugAssign, ast.For, ast.If, ast.Expr,
    ast.Pass, ast.Break, ast.Continue, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Name, ast.Constant, ast.List,
    ast.Tuple, ast.Dict, ast.Set, ast.Subscript, ast.Slice, ast.Attribute, ast.Call, ast.keyword, ast.ListComp, ast.SetComp,
    ast.DictComp, ast.GeneratorExp, ast.comprehension, ast.IfExp, ast.JoinedStr, ast.FormattedValue, ast.Load, ast.Store,
    ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.Add, ast.Sub, ast.Mult, ast.Mod, ast.USub, ast.FloorDiv,
}
UI_API = ("windows", "find", "children", "parent", "info", "value", "set_value", "invoke", "select", "wait", "log", "fail")
_HELPERS = {"lower", "upper", "casefold", "strip", "lstrip", "rstrip", "split", "rsplit", "startswith", "endswith", "replace",
            "find", "rfind", "get", "append", "extend", "items", "keys", "values", "join", "count", "index", "partition",
            "rpartition", "isdigit", "title",
            # plain container helpers (only data objects exist inside a method: these reach nothing outside it). Live
            # 2026-11-08: a third of the learner's methods were refused for '.update' and friends before they ever ran.
            "update", "pop", "insert", "remove", "setdefault", "copy", "sort", "isalpha", "isalnum", "isspace",
            "add", "discard", "union", "intersection", "difference"}
_BUILTINS = {"len": len, "range": None, "enumerate": enumerate, "str": str, "int": int, "any": any, "all": all, "min": min,
             "max": max, "sorted": sorted, "list": list, "dict": dict, "set": set, "reversed": reversed, "zip": zip,
             "True": True, "False": False, "None": None}

API_DOC = """Your method is `def run(ui, params):` and returns a short sentence saying what it did. Only these `ui` calls exist:
  ui.windows() -> list of top-level windows: {"id", "title", "process", "class"}
  ui.find(within_id, role="", name="", contains="") -> every element under that window/element (any depth) whose UIA control
      type equals role (e.g. "TabItem", "Button", "Tab", "Pane") and whose name equals `name` / contains `contains` (case-insensitive)
  ui.children(id) -> direct children, ui.parent(id) -> the parent element or None
  ui.info(id) -> fresh {"id", "role", "name", "exists"} for an element (exists False once it is gone)
  ui.value(id) -> the element's text VALUE (read-only), e.g. an Edit box's contents: '' when it has none
  ui.set_value(id, text) -> replaces the text of an Edit box (only an Edit box) with `text`
  ui.invoke(id) -> presses a button/element through UI Automation (no mouse), ui.select(id) -> selects a tab/list item
  ui.wait(seconds) -> waits up to 2 seconds, ui.log(text) -> notes for the trace, ui.fail(reason) -> stop: the task can't be done
Every element is a dict {"id", "role", "name", "window"}. There is NO keyboard, mouse, file, network or process access.
Allowed Python: assignments, if/elif/else, `for` loops, comprehensions, string methods (lower, casefold, strip, split,
startswith, endswith, replace, find, partition, join, count, ...), list/dict/set methods (append, extend, get, items, keys,
values, update, pop, setdefault, sort, add, ...), len, range, enumerate, zip, any, all, min, max, sorted, reversed, list,
dict, set, str, int. Methods that break these rules are REFUSED before they run:
  NO `while` loops         -> write `for attempt in range(5):` and `break` when done
  NO helper functions      -> everything goes inside run(); repeat small code instead of defining a function
  NO imports, lambda, try, class, global, or names starting with _
  NO try/except: there is nothing to catch. Check ui.info(id)["exists"] or the length of a ui.find(...) result, and stop with ui.fail(reason)
"""


UI_CAP_DOC = """`ui` — Windows UI Automation (developer-built primitive), for apps that have no better API:
  ui.windows() -> [{"id", "title", "process", "class"}]; ui.find(within_id, role="", name="", contains="") -> elements;
  ui.children(id), ui.parent(id), ui.info(id) -> {"id", "role", "name", "exists"}, ui.value(id) -> text value,
  ui.invoke(id) presses it, ui.select(id) selects it. Every element is {"id", "role", "name", "window"}."""

UTIL_DOC = """`util` — util.wait(seconds) waits up to 2 s, util.log(text) notes something for the trace,
  util.fail(reason) stops: the task cannot be done (an honest refusal is better than a wrong action)."""

PROCEDURE_DOC = """A procedure is plain Python with up to three top-level functions, each taking the capabilities you
depend on (in the order of your `dependencies`) and then `params`:
  def run(<caps>, params):            the actions; returns a short sentence saying what it did
  def verify(<caps>, params):         OPTIONAL: re-reads the state and returns True when the expected result holds
  def recover(<caps>, params, error): OPTIONAL: called once if run() raised; may undo/repair, then run() is tried again
All three receive the SAME params dict (it starts with only the inputs), so run() may record what it saw for verify().
Allowed: assignments, if/elif/else, `for` loops, comprehensions, string/list/dict/set methods, len, range, enumerate, zip,
any, all, min, max, sorted, reversed, list, dict, set, str, int. NOT allowed: while, imports, lambda, try, class, global,
inner def, names starting with _, calling run/verify/recover yourself. No keyboard, mouse, file, network or process access."""


class BadMethod(ValueError):
    pass


class MethodFailed(RuntimeError):
    """The method itself said the task can't be done (ui.fail)."""


class Budget(RuntimeError):
    pass


def check(source: str) -> ast.Module:
    if not isinstance(source, str) or not source.strip():
        raise BadMethod("empty method")
    if len(source) > MAX_SOURCE:
        raise BadMethod(f"method longer than {MAX_SOURCE} characters")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise BadMethod(f"syntax error line {error.lineno}: {error.msg}") from None
    problems = []
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(tree.body) != 1 or len(fns) != 1 or fns[0].name != "run" or [a.arg for a in fns[0].args.args] != ["ui", "params"]:
        problems.append("the file must contain exactly one function: def run(ui, params):")
    for node in ast.walk(tree):
        if type(node) not in _NODES:
            problems.append(f"line {getattr(node, 'lineno', '?')}: {type(node).__name__} is not allowed")
        elif isinstance(node, ast.FunctionDef) and node is not (fns[0] if fns else None):
            problems.append(f"line {node.lineno}: only the one run() function is allowed")
        elif isinstance(node, ast.FunctionDef) and (node.decorator_list or node.args.defaults or node.args.vararg or node.args.kwarg):
            problems.append("run() takes exactly (ui, params) with no decorators or defaults")
        elif isinstance(node, ast.Name) and ((node.id.startswith("_") and node.id != "_") or node.id in ("ui_", "exec", "eval", "open", "getattr")):
            problems.append(f"line {node.lineno}: name {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                problems.append(f"line {node.lineno}: attribute {node.attr!r} is not allowed")
            elif isinstance(node.value, ast.Name) and node.value.id == "ui":
                if node.attr not in UI_API:
                    problems.append(f"line {node.lineno}: ui.{node.attr} does not exist (only {', '.join(UI_API)})")
            elif node.attr not in _HELPERS:
                problems.append(f"line {node.lineno}: .{node.attr} is not allowed")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in ("ui", "params"):
            problems.append(f"line {node.lineno}: do not reassign {node.id}")
    if problems:
        raise BadMethod("; ".join(dict.fromkeys(problems)))
    return tree


PROC_FUNCS = ("run", "verify", "recover")


def check_procedure(source: str, caps) -> ast.Module:
    """The procedure form (core/skillspec): top-level run/verify/recover over named developer-built capabilities."""
    from core import capabilities
    caps = list(caps)
    if not caps or len(set(caps)) != len(caps):
        raise BadMethod("dependencies must name at least one capability, each once")
    bad = [c for c in caps if c not in capabilities.usable()]
    if bad:
        raise BadMethod(f"capability {', '.join(bad)} is not available (usable: {', '.join(capabilities.usable())})")
    if not isinstance(source, str) or not source.strip():
        raise BadMethod("empty procedure")
    if len(source) > MAX_SOURCE:
        raise BadMethod(f"procedure longer than {MAX_SOURCE} characters")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise BadMethod(f"syntax error line {error.lineno}: {error.msg}") from None
    problems, apis = [], {c: capabilities.api(c) for c in caps}
    fns = {}
    for n in tree.body:
        if not isinstance(n, ast.FunctionDef) or n.name not in PROC_FUNCS or n.name in fns:
            problems.append(f"line {getattr(n, 'lineno', '?')}: only the top-level functions run, verify, recover are allowed (each once)")
            continue
        fns[n.name] = n
        want = caps + ["params"] + (["error"] if n.name == "recover" else [])
        if [a.arg for a in n.args.args] != want:
            problems.append(f"def {n.name} must take ({', '.join(want)})")
        if n.decorator_list or n.args.defaults or n.args.vararg or n.args.kwarg or n.args.kwonlyargs:
            problems.append(f"{n.name}() takes no decorators, defaults or *args")
    if "run" not in fns:
        problems.append("the procedure needs def run(...)")
    for node in ast.walk(tree):
        if type(node) not in _NODES:
            problems.append(f"line {getattr(node, 'lineno', '?')}: {type(node).__name__} is not allowed")
        elif isinstance(node, ast.FunctionDef) and fns.get(node.name) is not node:
            problems.append(f"line {node.lineno}: inner functions are not allowed")
        elif isinstance(node, ast.Name):
            if (node.id.startswith("_") and node.id != "_") or node.id in ("exec", "eval", "open", "getattr") + PROC_FUNCS:
                problems.append(f"line {node.lineno}: name {node.id!r} is not allowed")
            elif isinstance(node.ctx, ast.Store) and node.id in caps + ["params"]:
                problems.append(f"line {node.lineno}: do not reassign {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                problems.append(f"line {node.lineno}: attribute {node.attr!r} is not allowed")
            elif isinstance(node.value, ast.Name) and node.value.id in apis:
                if node.attr not in apis[node.value.id]:
                    problems.append(f"line {node.lineno}: {node.value.id}.{node.attr} does not exist "
                                    f"(only {', '.join(apis[node.value.id])})")
            elif isinstance(node.value, ast.Name) and node.value.id in ("ui", "browser", "util", "bpy", "unreal", "vision"):
                problems.append(f"line {node.lineno}: {node.value.id} is not in your dependencies")
            elif node.attr not in _HELPERS:
                problems.append(f"line {node.lineno}: .{node.attr} is not allowed")
    if problems:
        raise BadMethod("; ".join(dict.fromkeys(problems)))
    return tree


def capabilities_used(source: str) -> set:
    """Which capability providers a procedure actually calls (for dependency honesty checks)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id in ("ui", "browser", "util", "bpy", "unreal", "vision")}


class _Meter:
    def __init__(self, max_calls, deadline):
        self.max, self.deadline, self.calls, self.trace = max_calls, deadline, 0, []


class _Cap:
    """One capability object as a procedure sees it: every call counted against the shared budget and traced."""

    def __init__(self, name, impl, names, meter):
        self._name, self._impl, self._m = name, impl, meter
        for n in names:
            setattr(self, n, self._wrap(n))

    def _wrap(self, op):
        def call(*args, **kwargs):
            m = self._m
            m.calls += 1
            if m.calls > m.max:
                raise Budget(f"more than {m.max} capability calls")
            if time.monotonic() > m.deadline:
                raise Budget("procedure ran out of time")
            if op == "fail":
                raise MethodFailed(str(args[0] if args else kwargs.get("reason", "the procedure gave up"))[:300])
            if op == "log":
                m.trace.append(str(args[0] if args else "")[:300])
                return None
            out = getattr(self._impl, op)(*args, **kwargs)
            if len(m.trace) < 80:
                shown = ", ".join([repr(a) if not isinstance(a, dict) else f"id={a.get('id')}" for a in args] +
                                  [f"{k}={v!r}" for k, v in kwargs.items()])
                m.trace.append(f"{self._name}.{op}({shown}) -> {_summary(out)}")
            return out
        return call


class _Util:
    def wait(self, seconds=0.5):
        time.sleep(max(0.0, min(2.0, float(seconds))))


def run_procedure(source: str, impls: dict, caps, params: dict, max_calls: int = MAX_CALLS, seconds: float = MAX_SECONDS) -> dict:
    """Run a checked procedure. impls = {"browser": BrowserAPI, "ui": UIA, ...}; util is always provided by the host.
    -> {"ok", "message", "error", "error_type", "failed_in", "recovered", "self_check", "calls", "trace"}. Never raises."""
    from core import capabilities
    try:
        tree = check_procedure(source, caps)
    except BadMethod as error:
        return {"ok": False, "message": "", "error": f"BadMethod: {error}"[:400], "error_type": "BadMethod", "failed_in": "check",
                "recovered": False, "self_check": None, "calls": 0, "trace": []}
    meter = _Meter(max_calls, time.monotonic() + seconds)
    objs = [_Cap(c, impls["util"] if c == "util" and "util" in impls else (_Util() if c == "util" else impls[c]),
                 capabilities.api(c), meter) for c in caps]
    env = {"__builtins__": {**_BUILTINS, "range": _range}}
    out = {"ok": False, "message": "", "error": "", "error_type": "", "failed_in": "", "recovered": False, "self_check": None}
    exec(compile(tree, "<learned procedure>", "exec"), env)          # noqa: S102 - whitelisted AST, empty builtins
    p = dict(params)
    try:
        out["message"] = str(env["run"](*objs, p) or "")[:300]
        out["ok"] = True
    except MethodFailed as error:
        out.update(error=f"MethodFailed: {error}"[:400], error_type="MethodFailed", failed_in="run")
    except Exception as error:  # noqa: BLE001 - the procedure's own error is evidence
        out.update(error=f"{type(error).__name__}: {error}"[:400], error_type=type(error).__name__, failed_in="run")
        if "recover" in env and not isinstance(error, Budget):
            try:
                env["recover"](*objs, p, out["error"])
                out["message"] = str(env["run"](*objs, p) or "")[:300]
                out.update(ok=True, recovered=True, error="", error_type="", failed_in="")
            except Exception as again:  # noqa: BLE001
                out.update(error=f"{type(again).__name__}: {again} (after recovery)"[:400], error_type=type(again).__name__,
                           failed_in="recover")
    if out["ok"] and "verify" in env:
        try:
            out["self_check"] = bool(env["verify"](*objs, p))
        except Exception as error:  # noqa: BLE001
            out["self_check"] = False
            out["self_check_error"] = f"{type(error).__name__}: {error}"[:300]
    out["calls"], out["trace"] = meter.calls, meter.trace
    return out


class _Counted:
    """Wraps the ui object: every primitive is counted and the deadline is enforced between calls."""

    def __init__(self, ui, max_calls, deadline):
        self._ui, self._max, self._deadline, self.calls, self.trace = ui, max_calls, deadline, 0, []
        for name in UI_API:
            setattr(self, name, self._wrap(name))

    def _wrap(self, name):
        def call(*args, **kwargs):
            self.calls += 1
            if self.calls > self._max:
                raise Budget(f"more than {self._max} ui calls")
            if time.monotonic() > self._deadline:
                raise Budget("method ran out of time")
            if name == "fail":
                raise MethodFailed(str(args[0] if args else kwargs.get("reason", "the method gave up"))[:300])
            if name == "log":
                self.trace.append(str(args[0] if args else "")[:300])
                return None
            out = getattr(self._ui, name)(*args, **kwargs)
            if len(self.trace) < 60:                   # what the method asked and what it got back: the experiment's observations
                shown = ", ".join([repr(a) if not isinstance(a, dict) else f"id={a.get('id')}" for a in args] +
                                  [f"{k}={v!r}" for k, v in kwargs.items()])
                self.trace.append(f"ui.{name}({shown}) -> {_summary(out)}")
            return out
        return call


def _summary(out):
    if isinstance(out, list):
        names = [str(o.get("title") or o.get("name") or "")[:50] if isinstance(o, dict) else str(o)[:30] for o in out[:6]]
        return f"{len(out)} item(s)" + (f": {names}" if names else "") + (" ..." if len(out) > 6 else "")
    if isinstance(out, dict):
        return "{" + ", ".join(f"{k}: {str(v)[:40]!r}" for k, v in out.items()) + "}"
    return repr(out)[:60]


def _range(*args):
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise Budget(f"range larger than {MAX_RANGE}")
    return r


def run(source: str, ui, params: dict, max_calls: int = MAX_CALLS, seconds: float = MAX_SECONDS) -> dict:
    """-> {"ok", "message", "error", "error_type", "calls", "trace"}. Never raises for the method's own faults."""
    tree = check(source)
    counted = _Counted(ui, max_calls, time.monotonic() + seconds)
    env = {"__builtins__": {**_BUILTINS, "range": _range}}
    try:
        exec(compile(tree, "<learned method>", "exec"), env)          # noqa: S102 - whitelisted AST, empty builtins, see check()
        message = env["run"](counted, dict(params))
        return {"ok": True, "message": str(message or "")[:300], "error": "", "error_type": "", "calls": counted.calls,
                "trace": counted.trace}
    except Exception as error:  # noqa: BLE001 - the method's own error is evidence, not a crash
        return {"ok": False, "message": "", "error": f"{type(error).__name__}: {error}"[:400], "error_type": type(error).__name__,
                "calls": counted.calls, "trace": counted.trace}


class UIA:
    """The real primitives, on whatever desktop this thread is attached to (the lab desktop in practice, the owner's in production)."""

    def __init__(self):
        from pywinauto import Desktop
        self._desktop = Desktop(backend="uia")
        self._els, self._ids = {}, {}

    def _put(self, wrapper, window=0):
        key = getattr(wrapper.element_info, "runtime_id", None) or id(wrapper)
        key = tuple(key) if isinstance(key, (list, tuple)) else key
        if key not in self._ids:
            self._ids[key] = len(self._els) + 1
            self._els[self._ids[key]] = (wrapper, window)
        eid = self._ids[key]
        info = wrapper.element_info
        return {"id": eid, "role": info.control_type or "", "name": info.name or "", "window": window or eid}

    def _get(self, eid):
        try:
            return self._els[int(eid if not isinstance(eid, dict) else eid["id"])]
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"no element with id {eid!r}") from None

    def windows(self):
        import psutil
        out = []
        for w in self._desktop.windows():
            try:
                if not w.is_visible() or not w.window_text():
                    continue
                try:
                    process = psutil.Process(w.process_id()).name()
                except Exception:  # noqa: BLE001
                    process = ""
                row = self._put(w)
                self._els[row["id"]] = (w, row["id"])
                out.append({"id": row["id"], "title": row["name"], "process": process, "class": w.element_info.class_name or ""})
            except Exception:  # noqa: BLE001 - a window that vanished mid-enumeration
                continue
        return out

    def find(self, within_id=None, role="", name="", contains="", within=None):
        wrapper, window = self._get(within_id if within_id is not None else within)
        kw = {"control_type": role} if role else {}
        out = []
        for d in wrapper.descendants(**kw):
            n = (d.element_info.name or "")
            if name and n.casefold() != str(name).casefold():
                continue
            if contains and str(contains).casefold() not in n.casefold():
                continue
            out.append(self._put(d, window))
            if len(out) >= 300:
                break
        return out

    def hwnd_of(self, eid):
        """HOST ONLY (not in UI_API, so no method can call it): the native window handle behind an element id, 0 when it has none."""
        wrapper, _ = self._get(eid)
        try:
            return int(wrapper.element_info.handle or 0)
        except Exception:  # noqa: BLE001
            return 0

    def children(self, eid=None, id=None):
        eid = eid if eid is not None else id
        wrapper, window = self._get(eid)
        return [self._put(c, window) for c in wrapper.children()][:300]

    def parent(self, eid=None, id=None):
        eid = eid if eid is not None else id
        wrapper, window = self._get(eid)
        p = wrapper.parent()
        return self._put(p, window) if p is not None else None

    def info(self, eid=None, id=None):
        eid = eid if eid is not None else id
        wrapper, window = self._get(eid)
        try:
            alive = wrapper.element_info.element.CurrentProcessId is not None and wrapper.is_visible() is not None
            name = wrapper.element_info.name or ""
        except Exception:  # noqa: BLE001 - gone
            return {"id": int(eid if not isinstance(eid, dict) else eid["id"]), "role": "", "name": "", "exists": False}
        return {**self._put(wrapper, window), "exists": bool(alive)}

    def value(self, eid=None, id=None):
        """Read-only: the element's UI Automation Value pattern (an edit box's text, e.g. a browser address bar)."""
        eid = eid if eid is not None else id
        wrapper, _ = self._get(eid)
        try:
            return str(wrapper.iface_value.CurrentValue or "")[:500]
        except Exception:  # noqa: BLE001 - no Value pattern on this element
            try:
                return str(wrapper.legacy_properties().get("Value") or "")[:500]
            except Exception:  # noqa: BLE001
                return ""

    def set_value(self, eid=None, text="", id=None):
        """Replace an Edit box's text through the UI Automation Value pattern (no keyboard). Edit boxes only."""
        eid = eid if eid is not None else id
        wrapper, _ = self._get(eid)
        if (wrapper.element_info.control_type or "") != "Edit":
            raise ValueError("set_value only works on an Edit box")
        wrapper.iface_value.SetValue(str(text)[:4000])
        return True

    def invoke(self, eid=None, id=None):
        eid = eid if eid is not None else id
        wrapper, _ = self._get(eid)
        wrapper.iface_invoke.Invoke()
        return True

    def select(self, eid=None, id=None):
        eid = eid if eid is not None else id
        wrapper, _ = self._get(eid)
        wrapper.iface_selection_item.Select()
        return True

    def wait(self, seconds=0.5):
        time.sleep(max(0.0, min(2.0, float(seconds))))
