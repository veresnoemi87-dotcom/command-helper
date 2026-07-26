from setuptools import setup

setup(
    name="cmdh",
    version="1.0.0",
    description="A tiny, dependency-free command/build runner driven by a config.json",
    py_modules=["cmdh"],
    entry_points={
        "console_scripts": [
            "cmdh=cmdh:main",
        ],
    },
    python_requires=">=3.7",
)
