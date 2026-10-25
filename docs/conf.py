"""Sphinx documentation for eLabLink, by Thibaut Jacqmin."""

from pathlib import Path

project = "eLabLink"
author = "Thibaut Jacqmin"
copyright = "2026, Thibaut Jacqmin"
release = "0.1.0"

extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx_rtd_theme"]
autodoc_mock_imports = ["labmate"]
autodoc_typehints = "none"
autodoc_member_order = "bysource"
napoleon_google_docstring = True

root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
html_theme = "sphinx_rtd_theme"
html_title = "eLabLink — Your eLabFTW notebook, from Python"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_theme_options = {
    "collapse_navigation": False,
    "sticky_navigation": True,
    "navigation_depth": 2,
    "includehidden": True,
    "style_nav_header_background": "#f5f7f5",
}
if (Path(__file__).parent / "_static" / "elablink-logo.png").is_file():
    html_logo = "_static/elablink-logo.png"
