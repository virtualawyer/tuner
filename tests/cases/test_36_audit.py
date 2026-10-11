"""Task 9.2 -- a mechanical audit of the source tree.

Cheap checks that catch the regressions that would actually hurt on this
box: a shell invocation, an unlink outside the one guarded place, a second
database connection, a raw color, a secret in the state payload, or syntax
that will not run on the Debian python3.

Every rule is also run against a deliberately broken sample, so the audit
itself cannot silently stop working.
"""

import ast
import io
import json
import os
import re
import tokenize
import unittest

import harness
from tuner import server

PACKAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tuner")

# rule name -> (pattern, files allowed to match)
RULES = (
    ("shell invocation", re.compile(r"shell\s*=\s*True"), ()),
    ("os.system", re.compile(r"\bos\.system\s*\("), ()),
    ("subprocess outside the yt-dlp adapter",
     re.compile(r"\bsubprocess\b"), ("ytdlp.py",)),
    ("unlink outside retention",
     re.compile(r"\bos\.(unlink|remove)\s*\("), ("retention.py",)),
    ("rmtree outside retention and the worker's scratch cleanup",
     re.compile(r"\bshutil\.rmtree\s*\("), ("retention.py", "worker.py",
                                            "demo.py")),
    ("database connection outside db.py",
     re.compile(r"\bsqlite3\.connect\s*\("), ("db.py",)),
    ("datetime.UTC, which does not exist before Python 3.11",
     re.compile(r"\bdatetime\.UTC\b"), ()),
    ("innerHTML, which would let a video title become markup",
     re.compile(r"\binnerHTML\b"), ()),
)


def strip_prose(source):
    """Drop comments and docstrings, keeping every other string literal.

    Without this the audit reads its own explanations: a comment saying
    "never use os.system" would trip the os.system rule. Ordinary strings
    stay, because web.py's stylesheet and script live in them.
    """
    lines = source.splitlines()

    docstring_lines = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                end = getattr(first, "end_lineno", first.lineno)
                for number in range(first.lineno, end + 1):
                    docstring_lines.add(number)

    comment_at = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                row, column = token.start
                comment_at[row] = min(comment_at.get(row, column), column)
    except (tokenize.TokenError, IndentationError):
        pass

    kept = []
    for number, line in enumerate(lines, 1):
        if number in docstring_lines:
            continue
        if number in comment_at:
            line = line[:comment_at[number]]
        kept.append(line)
    return "\n".join(kept)


def source_files(raw=False):
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(PACKAGE, name), "r", encoding="utf-8") as handle:
            source = handle.read()
        yield name, (source if raw else strip_prose(source))


def check(name, source):
    """Every rule this file breaks."""
    found = []
    for rule, pattern, allowed in RULES:
        if name in allowed:
            continue
        if pattern.search(source):
            found.append(rule)
    return found


class RuleTest(unittest.TestCase):

    def test_the_tree_is_clean(self):
        offences = []
        for name, source in source_files():
            for rule in check(name, source):
                offences.append("%s: %s" % (name, rule))
        self.assertEqual(offences, [])

    def test_each_rule_catches_its_violation(self):
        # If a rule ever stops matching, this fails rather than the audit
        # quietly passing everything.
        samples = {
            "shell invocation": "subprocess.run(cmd, shell=True)",
            "os.system": "os.system('rm -rf /')",
            "subprocess outside the yt-dlp adapter": "import subprocess",
            "unlink outside retention": "os.unlink(path)",
            "rmtree outside retention and the worker's scratch cleanup":
                "shutil.rmtree(path)",
            "database connection outside db.py": "sqlite3.connect(path)",
            "datetime.UTC, which does not exist before Python 3.11":
                "datetime.datetime.now(datetime.UTC)",
            "innerHTML, which would let a video title become markup":
                "node.innerHTML = title;",
        }
        for rule, sample in samples.items():
            self.assertIn(rule, check("some_new_module.py", sample),
                          "the %r rule no longer catches its own sample" % rule)

    def test_the_allowances_are_narrow(self):
        # subprocess is allowed in exactly one file, and unlink in one.
        for rule, _, allowed in RULES:
            self.assertLessEqual(len(allowed), 3, rule)

    def test_prose_is_not_mistaken_for_code(self):
        sample = ('"""A docstring mentioning os.system and subprocess."""\n'
                  "# a comment mentioning shutil.rmtree and datetime.UTC\n"
                  "value = 1  # and sqlite3.connect(here)\n")
        self.assertEqual(check("some_new_module.py", strip_prose(sample)), [])

    def test_real_code_survives_the_stripping(self):
        sample = ('"""Docstring."""\n'
                  "import os\n"
                  "os.system('boom')  # trailing comment\n")
        self.assertIn("os.system",
                      check("some_new_module.py", strip_prose(sample)))

    def test_strings_that_are_not_docstrings_are_kept(self):
        # web.py holds its stylesheet and script in module-level strings.
        sample = 'SCRIPT = """node.innerHTML = title;"""\n'
        self.assertIn("innerHTML, which would let a video title become markup",
                      check("web.py", strip_prose(sample)))


class SyntaxTest(unittest.TestCase):

    def test_every_module_compiles_on_this_python(self):
        # The build machine runs 3.9 and the target runs 3.11+. Anything
        # that only parses on the newer one is a deployment trap.
        for name, source in source_files(raw=True):
            try:
                compile(source, name, "exec")
            except SyntaxError as exc:
                self.fail("%s does not compile: %s" % (name, exc))

    def test_no_statement_that_39_cannot_parse(self):
        # The compile test above only proves the running interpreter. CI
        # also runs this suite on 3.9; on anything newer, look for the
        # statements that parse here and not there.
        import ast
        newer = tuple(getattr(ast, name) for name in ("Match", "TryStar")
                      if hasattr(ast, name))
        for name, source in source_files(raw=True):
            for node in ast.walk(ast.parse(source, name)):
                if newer and isinstance(node, newer):
                    self.fail("%s line %d uses %s, which 3.9 cannot parse"
                              % (name, node.lineno, type(node).__name__))


class StyleTest(unittest.TestCase):

    def test_no_raw_color_outside_the_root_blocks(self):
        with harness.temp_app() as app:
            from tuner import web
            style = web.render(app.cfg).split("<style>")[1].split("</style>")[0]

        without_root = re.sub(r":root\{[^}]*\}", "", style)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", without_root), [])

    def test_the_page_loads_nothing_from_another_host(self):
        with harness.temp_app() as app:
            from tuner import web
            markup = web.render(app.cfg)
        for construct in ("<link", "<script src", "@import", 'src="http',
                          'href="http', "jsdelivr", "unpkg"):
            self.assertNotIn(construct, markup)


class SecretTest(unittest.TestCase):

    def test_the_api_key_never_reaches_the_state_payload(self):
        secret = "jellyfin-secret-key-0123456789"
        with harness.temp_app(jellyfin_url="http://127.0.0.1:8096",
                              jellyfin_api_key=secret) as app:
            body = json.dumps(server.build_state(
                server.App(app.cfg, app.conn)))

        self.assertNotIn(secret, body)
        self.assertIn("0123456789"[-4:], body)   # only the last four

    def test_the_api_key_never_reaches_the_page(self):
        secret = "jellyfin-secret-key-0123456789"
        with harness.temp_app(jellyfin_api_key=secret) as app:
            from tuner import web
            self.assertNotIn(secret, web.render(app.cfg))


class RouteTest(unittest.TestCase):

    def test_every_write_route_is_a_post_patch_or_delete(self):
        # A GET must never change anything, so nothing that mutates may be
        # reachable from do_GET.
        with open(os.path.join(PACKAGE, "server.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()

        get_body = source.split("def do_GET(self):")[1].split("def do_POST")[0]
        for mutating in ("add_channels", "remove_channel", "delete_one",
                         "apply_settings", "retry", "check_now"):
            self.assertNotIn(mutating, get_body,
                             "%s is reachable from a GET" % mutating)


if __name__ == "__main__":
    unittest.main()
