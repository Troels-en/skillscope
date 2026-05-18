"""Tests for skillscope. Standard library only — run with:

    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skillscope as ss  # noqa: E402


def write_skill(root, name, text):
    """Create <root>/<name>/SKILL.md with the given text; return its path."""
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "SKILL.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestParsing(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_well_formed(self):
        p = write_skill(self.tmp, "good",
                        "---\nname: good\ndescription: Use when testing.\n"
                        "---\n\n# Body\nline two\n")
        s = ss.parse_skill(p)
        self.assertEqual(s["name"], "good")
        self.assertEqual(s["description"], "Use when testing.")
        self.assertGreater(s["body_lines"], 0)

    def test_block_scalar_description(self):
        p = write_skill(self.tmp, "block",
                        "---\nname: block\ndescription: |\n"
                        "  First line of the description.\n"
                        "  Second line continues it.\n---\nbody\n")
        s = ss.parse_skill(p)
        self.assertIn("Second line", s["description"])

    def test_missing_closing_fence(self):
        p = write_skill(self.tmp, "broken",
                        "---\nname: broken\ndescription: no closing fence\n")
        s = ss.parse_skill(p)  # must not raise
        self.assertEqual(s["name"], "broken")

    def test_empty_file(self):
        p = write_skill(self.tmp, "empty", "")
        s = ss.parse_skill(p)  # must not raise
        self.assertEqual(s["name"], "empty")
        self.assertEqual(s["body_lines"], 0)

    def test_name_falls_back_to_dir(self):
        p = write_skill(self.tmp, "fromdir",
                        "---\ndescription: no name field\n---\nbody\n")
        s = ss.parse_skill(p)
        self.assertEqual(s["name"], "fromdir")

    def test_kebab_case_flags(self):
        p = write_skill(self.tmp, "manual",
                        "---\nname: manual\ndescription: d\n"
                        "disable-model-invocation: true\n---\nbody\n")
        s = ss.parse_skill(p)
        self.assertTrue(s["disable_model_invocation"])


class TestAnalysis(unittest.TestCase):

    def _skill(self, **kw):
        base = {"name": "s", "description": "", "when_to_use": "",
                "disable_model_invocation": False, "user_invocable": True,
                "allowed_tools": "", "body_lines": 10, "path": "x"}
        base.update(kw)
        return ss.analyse(base)

    def test_oversized_body_flagged(self):
        s = self._skill(description="d", body_lines=ss.BODY_LINE_LIMIT + 1)
        self.assertTrue(any("over" in w[1] for w in s["warnings"]))

    def test_missing_description_is_high(self):
        s = self._skill(description="")
        self.assertTrue(any(level == "high" for level, _ in s["warnings"]))

    def test_broad_trigger_word_boundary(self):
        # "many" must NOT trip the "any" rule.
        s = self._skill(description="Handles many ordinary files.")
        self.assertFalse(any("Broad trigger" in w[1] for w in s["warnings"]))
        s2 = self._skill(description="Use for any request at any time.")
        self.assertTrue(any("Broad trigger" in w[1] for w in s2["warnings"]))

    def test_when_to_use_feeds_triggers(self):
        s = self._skill(description="A tool.",
                        when_to_use='Use when "deploying to prod".')
        self.assertIn("deploying to prod", s["triggers"])


class TestCollisionsAndBudget(unittest.TestCase):

    def test_collision_detected(self):
        a = ss.analyse({"name": "a", "description":
                        "Scan the codebase for security vulnerabilities found.",
                        "when_to_use": "", "disable_model_invocation": False,
                        "user_invocable": True, "allowed_tools": "",
                        "body_lines": 5, "path": "a"})
        b = ss.analyse({"name": "b", "description":
                        "Scan the codebase for security vulnerabilities found.",
                        "when_to_use": "", "disable_model_invocation": False,
                        "user_invocable": True, "allowed_tools": "",
                        "body_lines": 5, "path": "b"})
        pairs = ss.find_collisions([a, b])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["score"], 1.0)

    def test_build_actions_ranks_and_verdicts(self):
        good = ss.analyse({"name": "good", "description": "A clear skill.",
                           "when_to_use": "", "disable_model_invocation": False,
                           "user_invocable": True, "allowed_tools": "",
                           "body_lines": 20, "path": "g"})
        actions, verdict = ss.build_actions([good], [])
        self.assertEqual(actions, [])
        self.assertIn("healthy", verdict)

        bad = ss.analyse({"name": "bad", "description": "",
                          "when_to_use": "", "disable_model_invocation": False,
                          "user_invocable": True, "allowed_tools": "",
                          "body_lines": 20, "path": "b"})
        actions, verdict = ss.build_actions([bad], [])
        self.assertTrue(actions)
        self.assertEqual(actions[0][1], "high")  # high ranked first
        self.assertIn("high", verdict)

    def test_html_injection_is_escaped(self):
        evil = '<script>alert("xss")</script>'
        s = ss.analyse({"name": evil, "description": evil, "when_to_use": "",
                        "disable_model_invocation": False,
                        "user_invocable": True, "allowed_tools": "",
                        "body_lines": 5, "path": "x", "scope": "personal",
                        "origin": ""})
        html = ss.render_html([s], [], {"used_pct": 10})
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
