"""Guards that the API specifications match the servers they describe.

The specs drifted from the code in five places before anyone noticed, and the drift was
only found by writing tests against the documented contract and watching them fail. That
is an expensive way to discover a stale document, so the agreement is checked directly.

Three properties are enforced:
  1. Every implemented `/api/...` route appears in the matching spec.
  2. Every route named in a spec is actually implemented.
  3. Every request parameter a spec documents is one the handler reads.

Only *request* parameters are compared. Response shapes are described in prose in the
specs and deliberately not parsed here.
"""
import os
import re
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

#: Each app (tagpup.web.app.create_app) and its spec.
SERVER_SPEC_PAIRS = (
    ("tagpup", os.path.join(WORKSPACE_DIR, "docs/SPEC_TAGPUP_GUI.md")),
    ("tuner", os.path.join(WORKSPACE_DIR, "docs/SPEC_TAGTUNER.md")),
)

#: How a Flask route reads its request: the JSON body as `body`, the query as
#: `request.args`.
FLASK_BODY = re.compile(r'body\.get\(\s*"([^"]+)"')
FLASK_QUERY = re.compile(r'(?:request\.args\.get|_int_arg)\(\s*"([^"]+)"')


def flask_routes(kind):
    """Each /api route of the Flask app for `kind`, with the parameters its view reads,
    following one level of the module's own helpers as implemented_routes does."""
    import inspect

    from tagpup.web import app as web

    app = web.create_app(kind)
    out = {}
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        view = app.view_functions[rule.endpoint]
        source = inspect.getsource(view)
        module = inspect.getmodule(view)
        for helper in set(re.findall(r"\b(_\w+)\(", source)):
            found = getattr(module, helper, None)
            if inspect.isfunction(found):
                source += "\n" + inspect.getsource(found)
        out[rule.rule] = {"handler": view.__name__, "body": set(FLASK_BODY.findall(source)),
                          "query": set(FLASK_QUERY.findall(source))}
    return out

ROUTE_DISPATCH = re.compile(r'path == "(/api/[^"]+)":\s*\n\s*self\.(\w+)\(')
BODY_PARAM = re.compile(r'data\.get\(\s*"([^"]+)"')
QUERY_PARAM = re.compile(r'query\.get\(\s*"([^"]+)"')

# A spec bullet looks like:  - `/api/thing?x=<y>`: Expects JSON body `{"a": int, ...}`. ...
SPEC_ROUTE = re.compile(r'^- `(/api/[^`?\s]+)([^`]*)`\s*:?(.*)$')
SPEC_BODY = re.compile(r'Expects JSON body `\{([^`]*)\}`')
SPEC_KEY = re.compile(r'"(\w+)"')
SPEC_QUERY = re.compile(r'[?&](\w+)=')


def implemented_routes(kind):
    """Map each of the app's /api routes to the request parameters its view reads."""
    out = {}
    for route, found in flask_routes(kind).items():
        out[route] = {"handler": found["handler"], "body": set(found["body"]), "query": set(found["query"])}
    return out


def documented_routes(spec_path):
    """Map each documented route to the request parameters the spec claims it takes."""
    out = {}
    for line in open(spec_path, encoding="utf-8"):
        match = SPEC_ROUTE.match(line.strip())
        if not match:
            continue
        route, query_part, description = match.groups()
        entry = out.setdefault(route, {"body": set(), "query": set()})
        entry["query"].update(SPEC_QUERY.findall(query_part))
        body_match = SPEC_BODY.search(description)
        if body_match:
            entry["body"].update(SPEC_KEY.findall(body_match.group(1)))
    return out


class TestSpecMatchesImplementation(unittest.TestCase):
    def test_every_implemented_route_is_documented(self):
        problems = []
        for server, spec in SERVER_SPEC_PAIRS:
            impl = set(implemented_routes(server))
            doc = set(documented_routes(spec))
            for route in sorted(impl - doc):
                problems.append(f"{os.path.basename(spec)} does not document {route}")
        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))

    def test_every_documented_route_exists(self):
        problems = []
        for server, spec in SERVER_SPEC_PAIRS:
            impl = set(implemented_routes(server))
            doc = set(documented_routes(spec))
            for route in sorted(doc - impl):
                problems.append(
                    f"{os.path.basename(spec)} documents {route}, "
                    f"which {os.path.basename(server)} does not implement"
                )
        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))

    def test_documented_request_parameters_are_read_by_the_handler(self):
        problems = []
        for server, spec in SERVER_SPEC_PAIRS:
            impl = implemented_routes(server)
            doc = documented_routes(spec)
            for route, documented in sorted(doc.items()):
                if route not in impl:
                    continue  # covered by test_every_documented_route_exists
                actual = impl[route]
                for kind in ("body", "query"):
                    unknown = documented[kind] - actual[kind]
                    if unknown:
                        problems.append(
                            f"{os.path.basename(spec)} {route}: documents {kind} "
                            f"parameter(s) {sorted(unknown)} that "
                            f"{actual['handler']}() never reads "
                            f"(it reads {sorted(actual[kind]) or 'none'})"
                        )
        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))

    def test_extractors_are_not_silently_matching_nothing(self):
        """A parser that stops matching would make the guards above pass vacuously."""
        for server, spec in SERVER_SPEC_PAIRS:
            impl = implemented_routes(server)
            doc = documented_routes(spec)
            self.assertGreater(len(impl), 10, f"no routes parsed from {server}")
            self.assertGreater(len(doc), 10, f"no routes parsed from {spec}")
            self.assertTrue(
                any(v["body"] or v["query"] for v in doc.values()),
                f"no request parameters parsed from {spec}",
            )


class TestSpecDatabaseSchemaMatchesCode(unittest.TestCase):
    """The schema tables in docs/DATABASE.md must match what a new library actually has.

    Read from a library made by tagpup.store.schema, not from the text of a module: the
    schema was made in four places, and a regular expression over one of them saw only
    its idea of it (docs/findings.md, #48).
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile

        from tagpup.store import db, schema

        folder = tempfile.mkdtemp(prefix="spec_schema_")
        try:
            db_path = os.path.join(folder, "library.db")
            schema.ensure(db_path)
            conn = db.connect(db_path)
            try:
                tables = [name for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    " AND name NOT LIKE 'sqlite_%'")]
                cls.columns = {table: {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
                               for table in tables}
            finally:
                conn.close()
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        with open(os.path.join(WORKSPACE_DIR, "docs/DATABASE.md"), encoding="utf-8") as f:
            cls.database_md = f.read()

    def test_documented_tables_are_the_tables_created(self):
        documented = set(re.findall(r"^### \d+\.\s+`(\w+)` Table", self.database_md, re.M))
        self.assertTrue(self.columns, "a new library has no tables")
        self.assertEqual(
            set(self.columns),
            documented,
            f"docs/DATABASE.md tables {sorted(documented)} do not match "
            f"the tables of a new library {sorted(self.columns)}",
        )

    def test_documented_columns_match_each_created_table(self):
        problems = []
        for table, columns in sorted(self.columns.items()):
            section = re.search(
                rf"### \d+\.\s+`{table}` Table(.*?)(?=\n### |\n---)",
                self.database_md,
                re.DOTALL,
            )
            if not section:
                continue  # covered by the table-level test
            documented = set(re.findall(r"^\| `(\w+)`", section.group(1), re.M))

            missing = columns - documented
            extra = documented - columns
            if missing:
                problems.append(f"docs/DATABASE.md `{table}` is missing column(s) {sorted(missing)}")
            if extra:
                problems.append(
                    f"docs/DATABASE.md `{table}` documents column(s) {sorted(extra)} "
                    "that the schema does not create"
                )

        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    unittest.main()
