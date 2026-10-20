# Copyright: (c) 2026, Pure Storage Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the purefb_connect module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import sys
import multiprocessing
import ctypes
from unittest.mock import Mock, patch, MagicMock

# Mock multiprocessing context for Windows
if sys.platform == "win32":
    original_get_context = multiprocessing.get_context

    def mock_get_context(method=None):
        if method == "fork":
            return original_get_context("spawn")
        return original_get_context(method)

    multiprocessing.get_context = mock_get_context

    # Mock ctypes LoadLibrary to prevent Windows errors
    original_load_library = ctypes.cdll.LoadLibrary

    def mock_load_library(name):
        if name is None or not isinstance(name, str):
            return MagicMock()
        try:
            return original_load_library(name)
        except (OSError, TypeError):
            return MagicMock()

    ctypes.cdll.LoadLibrary = mock_load_library

# Mock external dependencies before importing module
sys.modules["pypureclient"] = MagicMock()
sys.modules["pypureclient.flashblade"] = MagicMock()
sys.modules["urllib3"] = MagicMock()
sys.modules["distro"] = MagicMock()
# Mock Unix-specific modules for Windows compatibility
sys.modules["grp"] = MagicMock()
sys.modules["fcntl"] = MagicMock()
sys.modules["pwd"] = MagicMock()
sys.modules["syslog"] = MagicMock()
# Mock termios with required constants
mock_termios = MagicMock()
mock_termios.TCSAFLUSH = 2
sys.modules["termios"] = mock_termios
sys.modules["tty"] = MagicMock()

# Mock ansible_collections module structure
sys.modules["ansible_collections"] = MagicMock()
sys.modules["ansible_collections.everpure"] = MagicMock()
sys.modules["ansible_collections.everpure.flashblade"] = MagicMock()
sys.modules["ansible_collections.everpure.flashblade.plugins"] = MagicMock()
sys.modules["ansible_collections.everpure.flashblade.plugins.module_utils"] = (
    MagicMock()
)
sys.modules["ansible_collections.everpure.flashblade.plugins.module_utils.purefb"] = (
    MagicMock()
)
sys.modules["ansible_collections.everpure.flashblade.plugins.module_utils.common"] = (
    MagicMock()
)
sys.modules["ansible_collections.everpure.flashblade.plugins.module_utils.version"] = (
    MagicMock()
)
sys.modules[
    "ansible_collections.everpure.flashblade.plugins.module_utils.time_utils"
] = MagicMock()

import pytest
from ansible.module_utils.compat.version import LooseVersion

from plugins.modules.purefb_connect import break_connection


def _module(check_mode=False):
    module = Mock()
    module.params = {"context": None}
    module.check_mode = check_mode
    module.exit_json = Mock(side_effect=SystemExit)
    module.fail_json = Mock(side_effect=SystemExit)
    return module


def _blade():
    """A blade whose get_arrays().items is a real iterator, like the SDK's
    ItemIterator: it cannot be indexed, only iterated."""
    local = Mock(spec=["name"])
    local.name = "local-array"
    blade = Mock()
    blade.get_arrays.return_value.items = iter([local])
    blade.delete_array_connections.return_value = Mock(status_code=200)
    return blade


def _target():
    target = Mock()
    target.management_address = "10.0.0.2"
    target.remote = Mock(spec=["name"])
    target.remote.name = "peer-array"
    return target


class TestBreakConnection:
    @patch("plugins.modules.purefb_connect.LooseVersion", LooseVersion)
    @patch("plugins.modules.purefb_connect.get_rest_api_version", return_value="2.21")
    def test_disconnect_iterates_the_arrays_response(self, _version):
        """Regression: get_arrays().items was indexed with [0], which raises
        TypeError on the SDK iterator and made every disconnect crash."""
        module = _module()
        blade = _blade()

        with pytest.raises(SystemExit):
            break_connection(module, blade, _target())

        blade.delete_array_connections.assert_called_once_with(
            remote_names=["peer-array"]
        )
        module.exit_json.assert_called_once_with(changed=True)
        module.fail_json.assert_not_called()

    @patch("plugins.modules.purefb_connect.LooseVersion", LooseVersion)
    @patch("plugins.modules.purefb_connect.get_rest_api_version", return_value="2.21")
    def test_disconnect_check_mode_changes_nothing(self, _version):
        module = _module(check_mode=True)
        blade = _blade()

        with pytest.raises(SystemExit):
            break_connection(module, blade, _target())

        blade.delete_array_connections.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    @patch("plugins.modules.purefb_connect.LooseVersion", LooseVersion)
    @patch("plugins.modules.purefb_connect.get_rest_api_version", return_value="2.21")
    def test_disconnect_refused_from_the_target_side(self, _version):
        """Only the array that formed the connection holds the management
        address; the other side must fail cleanly rather than call delete."""
        module = _module()
        blade = _blade()
        target = _target()
        target.management_address = None

        with pytest.raises(SystemExit):
            break_connection(module, blade, target)

        module.fail_json.assert_called_once()
        blade.delete_array_connections.assert_not_called()
