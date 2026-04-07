"""Tests for test infrastructure helpers — RB-26, RB-16.

These test the test harness itself to prevent infrastructure bugs
from masking real application issues.
"""

import os
import subprocess
from unittest.mock import patch

import pytest


# ── RB-26: _run_on_docker_host shell operator preservation ─────────


class TestRunOnDockerHost:
    """RB-26: _run_on_docker_host must use arg list (shell=False) so
    cmd.exe on Windows doesn't mangle shell operators like '>'.

    The bug: shell=True on Windows passes through cmd.exe, which
    interprets '>' as output redirection. SQL queries with '> 1000'
    created empty files instead of being passed to SSH.
    """

    def test_greater_than_preserved_locally(self):
        """Shell operators pass through correctly with arg list."""
        from tests.claude.live.helpers import _run_on_docker_host

        with patch.dict(os.environ, {"CLAUDE_TEST_SSH": ""}), \
             patch("tests.claude.live.helpers.SSH_TARGET", ""):
            result = _run_on_docker_host('echo "value > 1000"', timeout=5)
            assert "> 1000" in result, (
                f"Greater-than operator mangled by shell. Got: {result}"
            )

    def test_uses_arg_list_not_shell_string(self):
        """subprocess.run is called with a list, not a string."""
        from tests.claude.live.helpers import _run_on_docker_host

        with patch("subprocess.run") as mock_run, \
             patch("tests.claude.live.helpers.SSH_TARGET", "user@host"):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="ok", stderr=""
            )
            _run_on_docker_host("some command")

            call_args = mock_run.call_args
            # First positional arg should be a list, not a string
            assert isinstance(call_args[0][0], list), (
                f"Expected arg list, got {type(call_args[0][0])}: {call_args[0][0]}"
            )
            # Should NOT have shell=True
            assert call_args.kwargs.get("shell") is not True, (
                "shell=True will cause cmd.exe mangling on Windows"
            )

    def test_ssh_target_passed_as_arg(self):
        """SSH invocation uses ['ssh', target, cmd] format."""
        from tests.claude.live.helpers import _run_on_docker_host

        with patch("subprocess.run") as mock_run, \
             patch("tests.claude.live.helpers.SSH_TARGET", "user@host"):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            _run_on_docker_host("test command")

            args = mock_run.call_args[0][0]
            assert args == ["ssh", "user@host", "test command"]


# ── RB-16: install_stategraph source vs wheel detection ────────────


class TestInstallStategraphSource:
    """RB-16: install_stategraph with source='local' must install from
    the source directory on the bind mount, not from stale pre-built
    wheels baked into the image.

    The fix: copies source to /tmp (RB-34: bind mount is :ro) and
    pip installs from the copy with --force-reinstall.
    """

    @pytest.mark.asyncio
    async def test_local_source_copies_to_tmp(self):
        """Local source is copied to /tmp before pip install."""
        from app.flows.install_stategraph import install_stategraph

        mock_config = {
            "packages": {
                "source": "local",
                "local_path": "/app/packages",
            }
        }

        with patch("app.config.async_load_config", return_value=mock_config), \
             patch("app.flows.install_stategraph.os.path.isdir", return_value=True), \
             patch("shutil.copytree") as mock_copy, \
             patch("shutil.rmtree") as mock_rmtree, \
             patch("app.flows.install_stategraph._run_pip") as mock_pip, \
             patch("app.flows.install_stategraph._record_package_install") as mock_record, \
             patch("app.stategraph_registry.scan", return_value=[]), \
             patch("app.flows.build_type_registry.clear_compiled_cache"):

            mock_pip.return_value = {"returncode": 0, "stdout": "ok", "stderr": ""}
            mock_record.return_value = None

            result = await install_stategraph("context-broker-ae")

        assert result["status"] == "installed"

        # Verify source was copied to /tmp
        mock_copy.assert_called_once()
        src, dst = mock_copy.call_args[0]
        assert src == "/app/packages/context-broker-ae/"
        assert dst == "/tmp/sg-install-context-broker-ae"

        # Verify pip was called with /tmp path, not original mount
        pip_cmd = mock_pip.call_args[0][0]
        assert "/tmp/sg-install-context-broker-ae" in pip_cmd, (
            f"pip should install from /tmp copy, got: {pip_cmd}"
        )
        assert "--force-reinstall" in pip_cmd

    @pytest.mark.asyncio
    async def test_local_source_not_found_returns_error(self):
        """Missing source directory returns error without crashing."""
        from app.flows.install_stategraph import install_stategraph

        mock_config = {
            "packages": {
                "source": "local",
                "local_path": "/app/packages",
            }
        }

        with patch("app.config.async_load_config", return_value=mock_config), \
             patch("app.flows.install_stategraph.os.path.isdir", return_value=False), \
             patch("shutil.rmtree"), \
             patch("shutil.copytree"):

            result = await install_stategraph("nonexistent-package")

        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_pypi_source_does_not_copy(self):
        """PyPI source installs directly without /tmp copy."""
        from app.flows.install_stategraph import install_stategraph

        mock_config = {
            "packages": {
                "source": "pypi",
            }
        }

        with patch("app.config.async_load_config", return_value=mock_config), \
             patch("app.flows.install_stategraph._run_pip") as mock_pip, \
             patch("app.flows.install_stategraph._record_package_install") as mock_record, \
             patch("app.stategraph_registry.scan", return_value=[]), \
             patch("app.flows.build_type_registry.clear_compiled_cache"):

            mock_pip.return_value = {"returncode": 0, "stdout": "ok", "stderr": ""}
            mock_record.return_value = None

            result = await install_stategraph("context-broker-ae")

        pip_cmd = mock_pip.call_args[0][0]
        assert "/tmp" not in " ".join(pip_cmd), (
            "PyPI source should not use /tmp copy"
        )
