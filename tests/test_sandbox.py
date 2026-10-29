import tempfile
import unittest

from baton import sandbox

TPL = "docker run --rm --network none -v {checkout}:/work sh -c {command}"


class SandboxTemplateTest(unittest.TestCase):
    def test_parse(self):
        text = f"# x\n## Sandbox\n<!-- Command: no -->\nCommand: {TPL}\n## Other\n"
        self.assertEqual(sandbox.parse_template(text), TPL)

    def test_parse_missing(self):
        self.assertIsNone(sandbox.parse_template("## Hosts\nCommand: x\n"))
        self.assertIsNone(sandbox.parse_template("## Sandbox\n<!-- Command: x -->\nCommand:\n"))

    def test_render_quotes(self):
        out = sandbox.render(TPL, "/a b", "echo 'hi'; ls")
        self.assertIn("-v '/a b':/work", out)
        self.assertIn("sh -c 'echo '\"'\"'hi'\"'\"'; ls'", out)

    def test_render_missing_placeholder(self):
        with self.assertRaises(sandbox.SandboxError):
            sandbox.render("docker run {checkout}", "/a", "x")
        with self.assertRaises(sandbox.SandboxError):
            sandbox.render("docker run {command}", "/a", "x")

    def test_load_template(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(sandbox.load_template("o", "r", home=home))
            p = sandbox.brief_path("o", "r", home=home)
            p.parent.mkdir(parents=True)
            p.write_text(f"## Sandbox\nCommand: {TPL}\n")
            self.assertEqual(sandbox.load_template("o", "r", home=home), TPL)


if __name__ == "__main__":
    unittest.main()
