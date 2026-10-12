"""``core.approot`` — the one place that knows where the app tree lives.

Runs with the REAL ``core`` package (this directory's conftest wipes the
stubs), so these exercise the same module ``core.config`` and
``core.env_defaults`` import at load time.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVER_DIR.parent


@pytest.fixture
def approot(monkeypatch):
    for var in (
        "OPENCOMPANY_APP_ROOT",
        "OPENCOMPANY_CLIENT_DIST",
        "OPENCOMPANY_ENV_FILE",
        "OPENCOMPANY_ENV_TEMPLATE",
    ):
        monkeypatch.delenv(var, raising=False)
    import core.approot as mod

    return importlib.reload(mod)


class TestDefaults:
    def test_app_root_is_the_checkout_root(self, approot):
        assert approot.app_root() == REPO_ROOT
        assert (approot.app_root() / ".env.template").is_file()

    def test_server_root_is_where_the_code_lives(self, approot):
        assert approot.server_root() == SERVER_DIR
        assert (approot.server_root() / "main.py").is_file()

    def test_derived_paths_follow_the_sibling_layout(self, approot):
        assert approot.client_dist() == REPO_ROOT / "client" / "dist"
        assert approot.env_template_path() == REPO_ROOT / ".env.template"
        assert approot.env_file_path() == REPO_ROOT / ".env"
        assert approot.package_json_path() == REPO_ROOT / "package.json"
        assert approot.example_workflows_root() == REPO_ROOT / ".opencompany"


class TestOverrides:
    def test_app_root_override_moves_every_derived_path(self, approot, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCOMPANY_APP_ROOT", str(tmp_path))
        assert approot.app_root() == tmp_path.resolve()
        assert approot.client_dist() == tmp_path.resolve() / "client" / "dist"
        assert approot.env_template_path() == tmp_path.resolve() / ".env.template"
        assert approot.env_file_path() == tmp_path.resolve() / ".env"
        assert approot.package_json_path() == tmp_path.resolve() / "package.json"
        assert approot.example_workflows_root() == tmp_path.resolve() / ".opencompany"

    def test_server_root_ignores_the_override(self, approot, monkeypatch, tmp_path):
        """A mis-set root must never make the backend look for its own
        config/ or nodejs/ somewhere else."""
        monkeypatch.setenv("OPENCOMPANY_APP_ROOT", str(tmp_path))
        assert approot.server_root() == SERVER_DIR

    def test_env_file_override_is_independent_of_the_root(self, approot, monkeypatch, tmp_path):
        """The desktop shell keeps the bundle read-only and points the
        operator's .env at its data dir."""
        monkeypatch.setenv("OPENCOMPANY_APP_ROOT", str(tmp_path / "bundle"))
        monkeypatch.setenv("OPENCOMPANY_ENV_FILE", str(tmp_path / "data" / "desktop.env"))
        assert approot.env_file_path() == (tmp_path / "data" / "desktop.env").resolve()
        assert approot.env_template_path() == (tmp_path / "bundle" / ".env.template").resolve()

    def test_client_dist_override(self, approot, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCOMPANY_CLIENT_DIST", str(tmp_path / "dist"))
        assert approot.client_dist() == (tmp_path / "dist").resolve()

    def test_blank_override_is_ignored(self, approot, monkeypatch):
        monkeypatch.setenv("OPENCOMPANY_APP_ROOT", "   ")
        assert approot.app_root() == REPO_ROOT

    def test_tilde_is_expanded(self, approot, monkeypatch):
        monkeypatch.setenv("OPENCOMPANY_APP_ROOT", "~/some-bundle")
        assert approot.app_root() == (Path.home() / "some-bundle").resolve()


class TestSpaRoute:
    """What a path that matched no built file gets: the SPA shell, or a 404."""

    @pytest.mark.parametrize("path", ["", "employees/w1", "login"])
    def test_app_paths_get_the_shell(self, approot, path):
        assert approot.is_spa_route(path)

    @pytest.mark.parametrize(
        "path",
        [
            # A dev-server module path: answered with the shell, the orb
            # engine was refused as a module ("MIME type of text/html").
            "src/features/home/orb/orbEngine.ts",
            "assets/orbEngine-DnFDXWr7.js",
            "assets/index.css",
            "favicon.ico",
            "../.env",
        ],
    )
    def test_missing_files_get_a_404(self, approot, path):
        assert not approot.is_spa_route(path)


class TestConsumers:
    def test_paths_project_root_follows_approot(self, approot, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCOMPANY_APP_ROOT", str(tmp_path))
        import core.paths as paths

        paths = importlib.reload(paths)
        assert paths.project_root() == tmp_path.resolve()
        (tmp_path / ".opencompany" / "workflows").mkdir(parents=True)
        assert paths.example_workflows_dir() == tmp_path.resolve() / ".opencompany" / "workflows"

    def test_settings_env_file_is_absolute_and_layered(self, approot):
        import core.config as config

        config = importlib.reload(config)
        env_file = config.Settings.model_config["env_file"]
        assert isinstance(env_file, tuple) and len(env_file) == 2
        template, user = (Path(p) for p in env_file)
        assert template.is_absolute() and user.is_absolute()
        assert template.name == ".env.template" and user.name == ".env"
        assert template == approot.env_template_path()
        assert user == approot.env_file_path()
