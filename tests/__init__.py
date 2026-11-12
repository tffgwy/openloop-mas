"""Test package for OpenLoop MAS.

Importing this package installs the session-wide test isolation (see
``tests/_isolation.py``). ``python -m unittest discover -s tests`` imports
every test module as ``tests.<name>``, so the isolation is active before
any test module runs. Without it the suite would read the developer's
real ``~/.mas/config.json`` and make live network calls.
"""

from . import _isolation  # noqa: F401  (import for side effect)
