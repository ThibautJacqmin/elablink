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
        "labmate": ["labmate", "matplotlib"],
        "docs": ["Sphinx>=8.2,<9"],
    },
)
