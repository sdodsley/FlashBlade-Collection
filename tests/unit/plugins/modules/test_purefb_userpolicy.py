# Copyright: (c) 2026, Pure Storage Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for purefb_userpolicy module."""

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

from plugins.modules.purefb_userpolicy import add_policy, list_policy, main


def _make_module(context="array1", **overrides):
    module = Mock()
    module.check_mode = False
    module.exit_json = Mock(side_effect=SystemExit)
    module.fail_json = Mock(side_effect=SystemExit)
    module.params = {
        "name": "myuser",
        "account": "myaccount",
        "policy": ["myaccount/second-policy"],
        "context": context,
        "state": "present",
    }
    module.params.update(overrides)
    return module


def _policy(name):
    policy = Mock()
    policy.name = name
    return policy


def _membership(policy_name):
    member = Mock()
    member.policy = _policy(policy_name)
    return member


def _make_blade(already_assigned=False):
    """Blade mock. API 2.24 (>= 2.17 CONTEXT_API_VERSION), so the context path is active."""
    blade = Mock()
    blade.get_versions.return_value.items = ["2.17", "2.24"]
    # Existing membership check: empty => not yet assigned; non-empty => already there
    existing = [Mock()] if already_assigned else []
    blade.get_object_store_users_object_store_access_policies.return_value.items = (
        existing
    )
    # Successful write
    blade.post_object_store_access_policies_object_store_users.return_value.status_code = (
        200
    )
    # Re-read after write (post-add policy list)
    blade.get_object_store_access_policies_object_store_users.return_value.items = []
    return blade


class TestPurefbUserpolicyAddPolicy:
    """Regression tests for add_policy (issue #574)."""

    @patch("plugins.modules.purefb_userpolicy.LooseVersion")
    @patch("plugins.modules.purefb_userpolicy._check_valid_policy", return_value=True)
    def test_add_issues_single_context_scoped_post(self, _valid, mock_loose_version):
        """On API >= 2.17 the add must issue exactly ONE context-scoped POST.

        Previously a second, non-context POST ran unconditionally (no else) and
        overwrote the checked result, so the context write was never verified and
        the membership silently failed to persist while still reporting changed.
        """
        module = _make_module(context="array1")
        blade = _make_blade(already_assigned=False)

        # API version check: context feature gate ON (2.17 <= api_version)
        mock_loose_version.return_value.__le__ = Mock(return_value=True)

        try:
            add_policy(module, blade)
        except SystemExit:
            pass

        post = blade.post_object_store_access_policies_object_store_users
        assert post.call_count == 1
        kwargs = post.call_args[1]
        assert kwargs.get("context_names") == ["array1"]
        assert kwargs.get("member_names") == ["myaccount/myuser"]
        assert kwargs.get("policy_names") == ["myaccount/second-policy"]

        module.exit_json.assert_called_once()
        assert module.exit_json.call_args[1]["changed"] is True
        module.fail_json.assert_not_called()

    @patch("plugins.modules.purefb_userpolicy.LooseVersion")
    @patch("plugins.modules.purefb_userpolicy._check_valid_policy", return_value=True)
    def test_add_is_idempotent_when_already_assigned(self, _valid, mock_loose_version):
        """If the policy is already assigned, no POST is issued and changed is False."""
        module = _make_module(context="array1")
        blade = _make_blade(already_assigned=True)

        # API version check: context feature gate ON (2.17 <= api_version)
        mock_loose_version.return_value.__le__ = Mock(return_value=True)

        try:
            add_policy(module, blade)
        except SystemExit:
            pass

        blade.post_object_store_access_policies_object_store_users.assert_not_called()
        module.exit_json.assert_called_once()
        assert module.exit_json.call_args[1]["changed"] is False


class TestPurefbUserpolicyShow:
    """state=show is read-only: it must list policies without reporting a change."""

    @patch("plugins.modules.purefb_userpolicy.LooseVersion")
    def test_show_user_policies_reports_no_change(self, mock_loose_version):
        module = _make_module(context="array1", state="show", policy=None)
        blade = Mock()
        blade.get_versions.return_value.items = ["2.17", "2.24"]
        res = blade.get_object_store_access_policies_object_store_users.return_value
        res.status_code = 200
        res.items = [
            _membership("pure:policy/full-access"),
            _membership("pure:policy/object-read"),
        ]
        mock_loose_version.return_value.__le__ = Mock(return_value=True)

        try:
            list_policy(module, blade)
        except SystemExit:
            pass

        kwargs = blade.get_object_store_access_policies_object_store_users.call_args[1]
        assert kwargs.get("member_names") == ["myaccount/myuser"]
        assert kwargs.get("context_names") == ["array1"]
        module.exit_json.assert_called_once()
        assert module.exit_json.call_args[1]["changed"] is False
        assert module.exit_json.call_args[1]["policy_list"] == [
            "pure:policy/full-access",
            "pure:policy/object-read",
        ]
        module.fail_json.assert_not_called()

    @patch("plugins.modules.purefb_userpolicy.LooseVersion")
    def test_show_all_policies_without_user_reports_no_change(self, mock_loose_version):
        """Array-wide show has no account/name; it must not build a user name."""
        module = _make_module(context="", state="show", name=None, account=None)
        module.params["policy"] = None
        blade = Mock()
        blade.get_versions.return_value.items = ["2.17", "2.24"]
        blade.get_object_store_access_policies.return_value.status_code = 200
        blade.get_object_store_access_policies.return_value.items = [
            _policy("pure:policy/full-access"),
            _policy("pure:policy/object-read"),
        ]
        mock_loose_version.return_value.__le__ = Mock(return_value=False)

        try:
            list_policy(module, blade)
        except SystemExit:
            pass

        blade.get_object_store_access_policies_object_store_users.assert_not_called()
        blade.get_object_store_access_policies.assert_called_once_with()
        module.exit_json.assert_called_once()
        assert module.exit_json.call_args[1]["changed"] is False
        assert module.exit_json.call_args[1]["policy_list"] == [
            "pure:policy/full-access",
            "pure:policy/object-read",
        ]

    @patch("plugins.modules.purefb_userpolicy.LooseVersion")
    @patch("plugins.modules.purefb_userpolicy.get_system")
    @patch("plugins.modules.purefb_userpolicy.AnsibleModule")
    def test_main_show_all_skips_user_lookup(
        self, mock_ansible_module, mock_get_system, mock_loose_version
    ):
        """main() used to concatenate account + name for the existence check even
        when both were None (array-wide show), which raised TypeError."""
        module = _make_module(context="", state="show", name=None, account=None)
        module.params["policy"] = None
        mock_ansible_module.return_value = module
        blade = Mock()
        blade.get_versions.return_value.items = ["2.17", "2.24"]
        blade.get_object_store_access_policies.return_value.status_code = 200
        blade.get_object_store_access_policies.return_value.items = [
            _policy("pure:policy/full-access")
        ]
        mock_get_system.return_value = blade
        mock_loose_version.return_value.__le__ = Mock(return_value=False)

        try:
            main()
        except SystemExit:
            pass

        blade.get_object_store_users.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once()
        assert module.exit_json.call_args[1]["changed"] is False
        assert module.exit_json.call_args[1]["policy_list"] == [
            "pure:policy/full-access"
        ]
