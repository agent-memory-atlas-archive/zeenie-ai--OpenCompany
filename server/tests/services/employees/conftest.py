"""Employee tests inherit the shared real_database fixture from root conftest.

Its implementation lives in tests.database_fixtures, registered as a pytest
plugin so controller queue tests and other sibling packages can share the same
isolated SQLite/PostgreSQL setup without importing this directory's conftest.
"""
