from pathlib import Path

from setuptools import find_packages, setup

BASE_DIR = Path(__file__).resolve().parent
README = (BASE_DIR / "README.md").read_text(encoding="utf-8")

setup(
    name="elabmate",
    version="0.1.0",
    description="Python helpers for eLabFTW and asynchronous Labmate acquisition uploads",
    long_description=README,
    long_description_content_type="text/markdown",
    author="Thibaut Jacqmin",
    maintainer="Thibaut Jacqmin",
    url="https://github.com/ThibautJacqmin/elabmate",
    project_urls={
        "Source": "https://github.com/ThibautJacqmin/elabmate",
        "Issues": "https://github.com/ThibautJacqmin/elabmate/issues",
    },
    license_expression="MIT",
    packages=find_packages(exclude=("tests", "examples")),
    python_requires=">=3.10",
    install_requires=[
        "elabapi-python",
        "urllib3",
    ],
    extras_require={
        "labmate": ["labmate", "matplotlib"],
        "docs": ["Sphinx>=8.2,<9"],
    },
)
