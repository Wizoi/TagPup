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
import ast
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

SERVER_SPEC_PAIRS = (
    (
        os.path.join(WORKSPACE_DIR, "scripts", "tagpup_server.py"),
        os.path.join(WORKSPACE_DIR, "SPEC_TAGPUP_GUI.md"),
    ),
    (
        os.path.join(WORKSPACE_DIR, "scripts", "tuner_server.py"),
        os.path.join(WORKSPACE_DIR, "SPEC_TAGTUNER.md"),
    ),
)

ROUTE_DISPATCH = re.compile(r'path == "(/api/[^"]+)":\s*\n\s*self\.(\w+)\(')
BODY_PARAM = re.compile(r'data\.get\(\s*"([^"]+)"')
QUERY_PARAM = re.compile(r'query\.get\(\s*"([^"]+)"')

# A spec bullet looks like:  - `/api/thing?x=<y>`: Expects JSON body `{"a": int, ...}`. ...
SPEC_ROUTE = re.compile(r'^- `(/api/[^`?\s]+)([^`]*)`\s*:?(.*)$')
SPEC_BODY = re.compile(r'Expects JSON body `\{([^`]*)\}`')
SPEC_KEY = re.compile(r'"(\w+)"')
SPEC_QUERY = re.compile(r'[?&](\w+)=')


def implemented_routes(server_path):
    """Map each dispatched route to the request parameters its handler reads."""
    src = open(server_path, encoding="utf-8").read()
    routes = dict(ROUTE_DISPATCH.findall(src))

    # Split the class body into per-method sources.
    chunks = re.split(r"\n    def (\w+)\(", src)
    handlers = {chunks[i]: chunks[i + 1] for i in range(1, len(chunks), 2)}

    out = {}
    for route, handler_name in routes.items():
        body = handlers.get(handler_name, "")
        out[route] = {
            "handler": handler_name,
            "body": set(BODY_PARAM.findall(body)),
            "query": set(QUERY_PARAM.findall(body)),
        }
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
    """The schema tables in DATABASE.md must match what the index actually creates."""

    def test_documented_tables_are_the_tables_created(self):
        index_src = open(
            os.path.join(WORKSPACE_DIR, "scripts", "index.py"), encoding="utf-8"
        ).read()
        created = set(
            re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", index_src, re.IGNORECASE)
        )

        database_md = open(
            os.path.join(WORKSPACE_DIR, "DATABASE.md"), encoding="utf-8"
        ).read()
        documented = set(re.findall(r"^### \d+\.\s+`(\w+)` Table", database_md, re.M))

        self.assertTrue(created, "no CREATE TABLE statements found in index.py")
        self.assertEqual(
            created,
            documented,
            f"DATABASE.md tables {sorted(documented)} do not match "
            f"the schema created in index.py {sorted(created)}",
        )

    def test_documented_columns_match_each_created_table(self):
        index_src = open(
            os.path.join(WORKSPACE_DIR, "scripts", "index.py"), encoding="utf-8"
        ).read()
        database_md = open(
            os.path.join(WORKSPACE_DIR, "DATABASE.md"), encoding="utf-8"
        ).read()

        problems = []
        for table, body in re.findall(
            r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\s*\)",
            index_src,
            re.IGNORECASE | re.DOTALL,
        ):
            columns = set()
            for raw in body.split("\n"):
                line = raw.strip().rstrip(",")
                if not line or line.upper().startswith(("FOREIGN KEY", "PRIMARY KEY", "UNIQUE")):
                    continue
                columns.add(line.split()[0])

            section = re.search(
                rf"### \d+\.\s+`{table}` Table(.*?)(?=\n### |\n---)",
                database_md,
                re.DOTALL,
            )
            if not section:
                continue  # covered by the table-level test
            documented = set(re.findall(r"^\| `(\w+)`", section.group(1), re.M))

            missing = columns - documented
            extra = documented - columns
            if missing:
                problems.append(f"DATABASE.md `{table}` is missing column(s) {sorted(missing)}")
            if extra:
                problems.append(
                    f"DATABASE.md `{table}` documents column(s) {sorted(extra)} "
                    "that the schema does not create"
                )

        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    unittest.main()
