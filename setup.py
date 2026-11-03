from pathlib import Path

from setuptools import find_packages, setup

BASE_DIR = Path(__file__).resolve().parent
README = (BASE_DIR / "README.md").read_text(encoding="utf-8")

setup(
    name="elablink",
    version="0.1.0",
    description="Python helpers for eLabFTW and asynchronous Labmate acquisition uploads",
    long_description=README,
    long_description_content_type="text/markdown",
    author="Thibaut Jacqmin",
    maintainer="Thibaut Jacqmin",
    url="https://github.com/ThibautJacqmin/eLabLink",
    project_urls={
        "Source": "https://github.com/ThibautJacqmin/eLabLink",
        "Issues": "https://github.com/ThibautJacqmin/eLabLink/issues",
    },
    license_expression="MIT",
    packages=find_packages(include=("elablink", "elablink.*")),
    python_requires=">=3.10",
    install_requires=[
        "elabapi-python",
        "urllib3",
        "tomli; python_version < '3.11'",
    ],
    extras_require={
        # Temporary pin to the tested PR; use the containing release once published.
        "labmate": [
            "labmate @ git+https://github.com/kyrylo-gr/labmate.git@"
            "7384ea7c2fe53b3fafdb2c1bdf627936a7a432a4",
            "matplotlib",
        ],
        "docs": ["Sphinx>=8.2,<9", "sphinx-rtd-theme>=3.1,<4"],
    },
)
