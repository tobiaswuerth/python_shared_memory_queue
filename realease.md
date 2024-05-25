1. update `pyproject.toml` version
2. `py -m build`
3. Push update to PyPI / TestPyPI:
    `py -m twine upload --repository testpypi dist/*`
     or
    `py -m twine upload --repository pypi dist/*`