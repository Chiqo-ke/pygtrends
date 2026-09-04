# Publishing Guide

This package is prepared for publication by **Chiqo-ke**.

## Before publishing

1. Confirm that the PyPI project name `gtrendspy` is available and that you control the intended GitHub repository.
2. Confirm that you own or have permission to redistribute all source files and dependencies.
3. Review the existing [LICENSE](LICENSE). It currently contains the original MIT copyright notice for `SDil`; do not replace that notice unless the copyright has been transferred or you have permission to do so.
4. Run the fixture tests and inspect the built artifacts.
5. Test the authenticated Edge workflow manually with a user-controlled, already-authenticated browser profile.

For the runtime setup, users must start Edge with CDP enabled, authenticate manually, open Google Trends Explore, and keep the browser running while the package is used. The package does not launch Edge or handle sign-in. See the authenticated setup and troubleshooting section in [README.md](README.md).

## Build and inspect

From the package directory:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python -m build
python -m twine check dist/*
```

The expected release version is currently `0.2.0`.

## Publish

Use a token supplied interactively or through the standard Twine environment configuration. Do not put a PyPI token in source code, notebooks, command history, or logs.

```powershell
python -m twine upload dist/*
```

For a safer first release, publish to TestPyPI first:

```powershell
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ trendspy
```

## Release verification

After publication, verify:

```powershell
python -m pip install --upgrade gtrendspy
python -c "import trendspy; print(trendspy.__version__)"
```

The authenticated Edge feature requires a local Edge process with CDP enabled. PyPI installation alone cannot provide or authenticate that browser session.