from setuptools import setup

setup(
    name="cmdh",
    version="2.5.0",
    description="A tiny command/build runner driven by a config.json",
    py_modules=["cmdh"],
    install_requires=[
        "requests>=2.0.0",
    ],
    entry_points={
        "console_scripts": [
            "cmdh=cmdh:main",
        ],
    },
    python_requires=">=3.7",
)
