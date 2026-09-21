import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "spec_trace"
ASSETS = PACKAGE / "web_assets"


class DashboardAssetSeparationTest(unittest.TestCase):
    def test_dashboard_uses_separate_html_css_and_javascript_files(self) -> None:
        for name in ("dashboard.html", "dashboard.css", "dashboard.js"):
            self.assertTrue((ASSETS / name).is_file(), name)

        dashboard_python = (PACKAGE / "dashboard.py").read_text(encoding="utf-8")
        html = (ASSETS / "dashboard.html").read_text(encoding="utf-8")
        css = (ASSETS / "dashboard.css").read_text(encoding="utf-8")
        javascript = (ASSETS / "dashboard.js").read_text(encoding="utf-8")

        self.assertNotIn("<style>", dashboard_python)
        self.assertNotIn("<script>", dashboard_python)
        self.assertNotIn("<style>", html)
        self.assertNotIn("<script>", html)
        self.assertIn('href="/assets/dashboard.css"', html)
        self.assertIn('src="/assets/dashboard.js"', html)
        self.assertIn(".detail-empty[hidden]", css)
        self.assertIn("async function load()", javascript)

    def test_python_server_exposes_assets_and_package_includes_them(self) -> None:
        web = (PACKAGE / "web.py").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('path == "/assets/dashboard.css"', web)
        self.assertIn('path == "/assets/dashboard.js"', web)
        self.assertNotIn('HTML = r"""', web)
        self.assertIn('"web_assets/*"', project)


if __name__ == "__main__":
    unittest.main()
