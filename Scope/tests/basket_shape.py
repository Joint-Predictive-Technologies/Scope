"""THE BASKET-RULE DETECTOR — the disease defined by its SHAPE, not by a list of names.

⚠️ NOT A TEST MODULE. It has no `test_` prefix on purpose, so pytest does not collect it;
`test_basket_rule_gate_class.py` drives it.

════════════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
════════════════════════════════════════════════════════════════════════════════════

A "basket rule" publishes an alert whose TICKER comes from a hardcoded lookup table
rather than from the event. `news → category → basket → tickers[0]` says nothing about
which company was involved; it says which company someone once typed next to that
category. Such a ticker must never complete a convergence.

The predecessor guard asserted exactly that — and did not deliver it. It identified a
rule by "the first quoted `RULE_*` literal in the file", walked `scripts/` only, and
recognised two table names. Measured against five planted members, **three passed**:

    C  a live rule whose docstring mentions another rule   -> identified as THAT rule
    D  an identical rule at the repo ROOT                  -> never walked
    E  a rule file with an unexpected name                 -> never walked

That is this codebase's recurring defect one level up: **a class identified by a fragile
projection** — a filename, a table name, a grep — instead of by what it *is*. So the
detector below never asks what anything is CALLED. It asks:

    (a) is there a module-level dict whose values are lists of strings, and
    (b) does an element of one of those value lists become an alert's TICKER?

⚠️ NO TABLE NAME AND NO FILE NAME APPEARS ANYWHERE IN THIS MODULE. That is asserted by
`test_the_detector_names_no_table_and_no_file`. If a name ever appears here, the guard
has quietly become a list again.

════════════════════════════════════════════════════════════════════════════════════
WHAT IT DELIBERATELY DOES *NOT* FLAG
════════════════════════════════════════════════════════════════════════════════════

* A basket-shaped dict that never reaches a ticker — sector-heat and keyword maps are
  the same SHAPE and are not the same THING. Clause (b) is what separates them.
* An INVERTED map — ticker as the KEY, evidence strings as the values. That is real
  attribution ("this document names Lockheed Martin → LMT"), and the value never
  becomes the ticker, so it is correctly ignored.
* A rule that derives its ticker from the filing or the award. No basket, no flag.
"""
from __future__ import annotations

import ast
import os

# Directories that hold no rule code. Everything else in the repo is walked, because
# rules live at the repo ROOT as well as in the scripts directory — the omission that
# let planted variant D through.
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "tests",
              "data", "static", "backups", "migrations"}

# The column an alert's ticker is written to, and the table alerts live in. These are
# schema facts, not rule names — the detector still knows nothing about any rule.
_TICKER_COLUMN = "ticker"
_ALERTS_TABLE = "alerts"

# Methods that put a value INTO a container, so the container inherits its taint.
_MUTATORS = {"append", "add", "extend", "insert", "update"}


# ── helpers ─────────────────────────────────────────────────────────────────

def _is_basket_literal(node: ast.AST) -> bool:
    """A dict mapping anything to a LIST OF STRINGS — the basket shape.

    The key type is deliberately unconstrained: one real basket is keyed on a tuple
    `(region, category)`, and a guard that assumed `str` keys would have missed it.
    """
    if not isinstance(node, ast.Dict) or not node.values:
        return False
    return all(
        isinstance(v, ast.List) and v.elts
        and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in v.elts)
        for v in node.values
    )


def _module_baskets(tree: ast.Module) -> set[str]:
    """Names bound at module level to a basket literal."""
    out: set[str] = set()
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name)):
            target = node.targets[0].id
        if target and _is_basket_literal(node.value):
            out.add(target)
    return out


def _imported_baskets(tree: ast.Module, repo: str) -> set[str]:
    """Names imported INTO this module that are baskets where they are defined.

    Two of the real basket rules never define a basket themselves — they import one.
    A detector that only looked at the module in front of it would clear both.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module or node.level:
            continue
        src = os.path.join(repo, node.module.replace(".", os.sep) + ".py")
        if not os.path.exists(src):
            continue
        try:
            defined = _module_baskets(ast.parse(open(src, encoding="utf-8").read()))
        except (SyntaxError, OSError):
            continue
        for alias in node.names:
            if alias.name in defined:
                out.add(alias.asname or alias.name)
    return out


def _split_top(s: str) -> list[str]:
    """Split on commas that are not inside parentheses or quotes."""
    out, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf).strip())
    return out


def _insert_columns(node: ast.AST) -> tuple[list[str], list[str]] | None:
    """`(columns, value expressions)` of an `INSERT INTO alerts (...) VALUES (...)`.

    Literal parts only, so an f-string or concatenated query yields None and the caller
    falls back to its other sinks rather than guessing.
    """
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return None
    sql = " ".join(node.value.split())
    low = sql.lower()
    if f"insert into {_ALERTS_TABLE}" not in low:
        return None
    try:
        after = sql[low.index(f"insert into {_ALERTS_TABLE}"):]
        cols = _split_top(after.split("(", 1)[1].split(")", 1)[0])
        rest = after.split(")", 1)[1]
        vals_src = rest[rest.lower().index("values"):]
        vals = _split_top(vals_src.split("(", 1)[1].rsplit(")", 1)[0])
    except (IndexError, ValueError):
        return None
    return [c.strip().lower() for c in cols], [v.strip() for v in vals]


def _sql_param_index(node: ast.AST, column: str) -> int | None:
    """Which element of the PARAMS TUPLE is bound to `column`.

    ⚠️ THIS IS NOT THE COLUMN INDEX, AND ASSUMING IT WAS MISSED THREE OF THE FIVE REAL
    BASKET RULES. They write `INSERT INTO alerts (rule, ticker, …) VALUES ('RULE_X', ?, …)`
    — the rule name is INLINE, so every placeholder after it sits one position left of
    its column. The parameter index is the number of `?` appearing before this column,
    and a column bound to a literal has no parameter at all.
    """
    parsed = _insert_columns(node)
    if parsed is None:
        return None
    cols, vals = parsed
    if column not in cols or len(vals) != len(cols):
        return None
    i = cols.index(column)
    if vals[i] != "?":
        return None                      # written as a literal, not from a variable
    return sum(1 for v in vals[:i] if v == "?")


def _sql_literal_for(node: ast.AST, column: str) -> str | None:
    """A column written as an inline literal — e.g. the rule name in the VALUES clause."""
    parsed = _insert_columns(node)
    if parsed is None:
        return None
    cols, vals = parsed
    if column not in cols or len(vals) != len(cols):
        return None
    v = vals[cols.index(column)]
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return None


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    return getattr(f, "attr", None) or getattr(f, "id", None)


# ── the analysis ────────────────────────────────────────────────────────────

class _ModuleScan:
    """One module: where baskets are, where tickers are written, and whether they meet."""

    def __init__(self, path: str, repo: str):
        self.path = path
        self.src = open(path, encoding="utf-8").read()
        self.tree = ast.parse(self.src)
        self.baskets = _module_baskets(self.tree) | _imported_baskets(self.tree, repo)
        self.funcs = {n.name: n for n in ast.walk(self.tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        # function name -> parameter names that end up written as a ticker. Seeded from
        # the direct sinks and grown to a fixpoint, because a rule may define its OWN
        # alert writer and pass the basket value through it — which is precisely how the
        # live violator evaded a detector that only knew the shared helper.
        self.sink_params: dict[str, set[str]] = {}
        # local function name -> the grade of the basket data it returns
        self.tainted_funcs: dict[str, str] = {}
        self._resolve()

    # -- taint ------------------------------------------------------------
    def _grade(self, node: ast.AST, local: dict) -> str | None:
        """How basket-derived this expression is: `LIST`, `ELEM`, or not at all.

        ⚠️ THE TWO GRADES ARE THE WHOLE PRECISION OF THIS DETECTOR, and collapsing them
        into one boolean made it accuse an honest rule. A basket's VALUE LIST passed to
        an opaque helper is just "some strings" — one real rule hands a list of search
        keywords to an HTTP fetch, and treating that call's RESULT as basket data walked
        the taint all the way to a ticker that came from the document. An ELEMENT is
        different: it is one symbol out of the table, and a transform of it (a
        normaliser, a formatter) is still that symbol.

        So: taint crosses an opaque call only at ELEM grade. LIST stops there.
        """
        if node is None:
            return None
        if isinstance(node, ast.Name):
            if node.id in self.baskets:
                return "BASKET"
            return local.get(node.id)
        if isinstance(node, ast.Subscript):
            g = self._grade(node.value, local)
            if g is None:
                return None
            if isinstance(node.slice, ast.Slice):
                return "LIST" if g in ("LIST", "BASKET") else g
            return "LIST" if g == "BASKET" else "ELEM"
        if isinstance(node, ast.Attribute):
            return self._grade(node.value, local)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            grades = [self._grade(e, local) for e in node.elts]
            return next((g for g in grades if g), None)
        if isinstance(node, ast.BoolOp):
            grades = [self._grade(v, local) for v in node.values]
            return next((g for g in grades if g), None)
        if isinstance(node, ast.IfExp):
            return self._grade(node.body, local) or self._grade(node.orelse, local)
        if isinstance(node, ast.JoinedStr):
            return next((g for v in node.values
                         for g in [self._grade(getattr(v, "value", None), local)] if g), None)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            inner = dict(local)
            for gen in node.generators:
                g = self._grade(gen.iter, local)
                if g in ("LIST", "BASKET"):
                    for n in _bound_names(gen.target):
                        inner[n] = "ELEM"
            return "LIST" if self._grade(node.elt, inner) else None
        if isinstance(node, ast.Call):
            name = _call_name(node)
            recv = getattr(node.func, "value", None)
            if name in ("get", "pop") and self._grade(recv, local) == "BASKET":
                return "LIST"                      # basket.get(key) -> the value list
            if name == "values" and self._grade(recv, local) == "BASKET":
                return "LIST"
            if name == "items" and self._grade(recv, local) == "BASKET":
                return "LIST"                      # refined per-half at the for-loop
            if name in self.tainted_funcs:
                return self.tainted_funcs[name]    # a local function returning basket data
            # An opaque call carries a SYMBOL onward (normalise, upper, strip) but does
            # not turn a list of basket strings into basket tickers.
            if any(self._grade(a, local) == "ELEM" for a in node.args):
                return "ELEM"
            return None
        return None

    def _tainted(self, node: ast.AST, local: dict) -> bool:
        return self._grade(node, local) is not None

    def _items_value_half(self, node: ast.For, local: dict) -> set[str] | None:
        """`for k, v in basket.items()` — the names bound to the VALUE half only.

        ⚠️ THIS IS THE LINE THAT SEPARATES A BASKET FROM REAL ATTRIBUTION, and getting
        it wrong made the detector accuse an honest rule. `.items()` yields `(key,
        value)`, and only the value half is basket data. A map written the other way up
        — TICKER as the key, evidence strings as the values — resolves a document to a
        company, which is the correct direction; its ticker comes from the KEY and must
        not be flagged.
        """
        it = node.iter
        if not (isinstance(it, ast.Call) and _call_name(it) == "items"
                and self._grade(getattr(it.func, "value", None), local) == "BASKET"):
            return None
        if isinstance(node.target, (ast.Tuple, ast.List)) and len(node.target.elts) == 2:
            return _bound_names(node.target.elts[1])
        return _bound_names(node.target)

    def _local_taint(self, fn: ast.AST, seed: dict | None = None) -> dict:
        """Local names inside `fn` that hold basket-derived data, with their grade.
        Iterated to a fixpoint so ordering inside the function cannot hide a flow."""
        local = dict(seed or {})
        for _ in range(8):
            before = dict(local)
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and self._grade(node.value, local):
                    g = self._grade(node.value, local)
                    for t in node.targets:
                        for n in _bound_names(t):
                            local[n] = g
                elif isinstance(node, ast.AnnAssign) and self._grade(node.value, local):
                    for n in _bound_names(node.target):
                        local[n] = self._grade(node.value, local)
                elif isinstance(node, ast.For):
                    half = self._items_value_half(node, local)
                    if half is not None:
                        for n in half:
                            local[n] = "LIST"
                    else:
                        g = self._grade(node.iter, local)
                        if g in ("LIST", "BASKET"):
                            for n in _bound_names(node.target):
                                local[n] = "ELEM"
                elif isinstance(node, ast.Call) and _call_name(node) in _MUTATORS:
                    # ⚠️ A CONTAINER FILLED BY MUTATION IS STILL THE BASKET'S DATA.
                    # The live violator builds its ticker list with `.append()` inside a
                    # loop, so a detector that only followed ASSIGNMENT saw an untainted
                    # empty list and cleared the one rule that was actually firing the
                    # gate.
                    recv = getattr(node.func, "value", None)
                    if (isinstance(recv, ast.Name)
                            and any(self._grade(a, local) for a in node.args)):
                        local[recv.id] = "LIST"
            if local == before:
                break
        return local

    # -- sinks ------------------------------------------------------------
    def _direct_ticker_args(self, call: ast.Call) -> list[ast.AST]:
        """Expressions this call would write into the ticker column."""
        out: list[ast.AST] = []
        idx = None
        for arg in call.args:
            idx = _sql_param_index(arg, _TICKER_COLUMN)
            if idx is not None:
                break
        if idx is not None:
            for arg in call.args:
                if isinstance(arg, (ast.Tuple, ast.List)) and len(arg.elts) > idx:
                    out.append(arg.elts[idx])
        for kw in call.keywords:
            if kw.arg == _TICKER_COLUMN:
                out.append(kw.value)
        return out

    def _resolve(self):
        """Grow the sink set and the tainted-function set together, to a fixpoint."""
        for _ in range(6):
            before = (dict(self.sink_params), dict(self.tainted_funcs))

            # (1) functions that RETURN basket data become tainted sources themselves
            for name, fn in self.funcs.items():
                local = self._local_taint(fn)
                for node in ast.walk(fn):
                    if isinstance(node, ast.Return):
                        g = self._grade(node.value, local)
                        if g:
                            self.tainted_funcs[name] = g

            # (2) a local function whose PARAMETER is written as a ticker is itself a
            #     ticker sink at that parameter — the wrapper case
            for name, fn in self.funcs.items():
                params = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    for expr in self._direct_ticker_args(node) + self._wrapped_args(node):
                        for p in params:
                            if self._tainted(expr, {p: "ELEM"}):
                                self.sink_params.setdefault(name, set()).add(p)

            if (dict(self.sink_params), dict(self.tainted_funcs)) == before:
                break

    def _wrapped_args(self, call: ast.Call) -> list[ast.AST]:
        """Arguments this call passes into a known wrapper's ticker parameter."""
        name = _call_name(call)
        if name not in self.sink_params:
            return []
        fn = self.funcs.get(name)
        if fn is None:
            return []
        names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
        out = []
        for i, arg in enumerate(call.args):
            if i < len(names) and names[i] in self.sink_params[name]:
                out.append(arg)
        for kw in call.keywords:
            if kw.arg in self.sink_params[name]:
                out.append(kw.value)
        return out

    # -- verdict ----------------------------------------------------------
    def basket_reaches_a_ticker(self) -> bool:
        if not self.baskets:
            return False
        scopes = [(fn, self._local_taint(fn)) for fn in self.funcs.values()]
        scopes.append((self.tree, self._local_taint(self.tree)))
        for scope, local in scopes:
            for node in ast.walk(scope):
                if not isinstance(node, ast.Call):
                    continue
                for expr in self._direct_ticker_args(node) + self._wrapped_args(node):
                    if self._tainted(expr, local):
                        return True
        return False

    # -- identity ---------------------------------------------------------
    def rule_names(self) -> set[str]:
        """Every rule name this module DECLARES, read from code, never from text.

        ⚠️ AST-ONLY, AND THAT IS THE FIX. The predecessor took the first quoted
        `RULE_*` literal anywhere in the file, so a docstring cross-reference —
        "supersedes the retired X path" — renamed a live rule to an excluded one and
        the assertion passed. A docstring is a bare expression statement; it is
        skipped here by construction, because only assignment targets, call keywords
        and call arguments are read.
        """
        found: set[str] = set()

        def _lit(node):
            return (node.value if isinstance(node, ast.Constant)
                    and isinstance(node.value, str) else None)

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.upper() == "RULE":
                        v = _lit(node.value)
                        if v:
                            found.add(v)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id.upper() == "RULE":
                    v = _lit(node.value)
                    if v:
                        found.add(v)
            elif isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "rule":
                        v = _lit(kw.value)
                        if v:
                            found.add(v)
                if _call_name(node) == "record_activity" and node.args:
                    v = _lit(node.args[0])
                    if v:
                        found.add(v)
                # a raw INSERT's rule column — inline literal, or from the params tuple
                for arg in node.args:
                    inline = _sql_literal_for(arg, "rule")
                    if inline:
                        found.add(inline)
                    idx = _sql_param_index(arg, "rule")
                    if idx is None:
                        continue
                    for other in node.args:
                        if isinstance(other, (ast.Tuple, ast.List)) and len(other.elts) > idx:
                            v = _lit(other.elts[idx])
                            if v:
                                found.add(v)
        return {f for f in found if f.upper().startswith("RULE")}


def _bound_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for e in target.elts:
            out |= _bound_names(e)
        return out
    return set()


# ── the sweep ───────────────────────────────────────────────────────────────

def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def scan_repo(repo: str | None = None) -> list[dict]:
    """Every module where a basket value becomes an alert ticker.

    Returns dicts of `{path, baskets, rule, ambiguous}`. `rule` is None and
    `ambiguous` explains why when the module's identity cannot be established from
    exactly one declaration — the caller MUST fail closed on those rather than
    treating them as clean.
    """
    repo = repo or repo_root()
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            try:
                scan = _ModuleScan(path, repo)
            except (SyntaxError, OSError, UnicodeDecodeError):
                continue
            if not scan.basket_reaches_a_ticker():
                continue
            names = scan.rule_names()
            rule = next(iter(names)) if len(names) == 1 else None
            out.append({
                "path": os.path.relpath(path, repo),
                "baskets": sorted(scan.baskets),
                "rule": rule,
                "ambiguous": (None if rule else
                              ("no rule declaration found" if not names
                               else f"conflicting declarations: {sorted(names)}")),
            })
    return sorted(out, key=lambda r: r["path"])
