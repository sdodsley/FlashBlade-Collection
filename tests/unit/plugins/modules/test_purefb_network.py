# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the purefb_network module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

# Mock external dependencies before importing module
sys.modules["pypureclient"] = MagicMock()
sys.modules["pypureclient.flashblade"] = MagicMock()
sys.modules["urllib3"] = MagicMock()
sys.modules["distro"] = MagicMock()
sys.modules["grp"] = MagicMock()
sys.modules["fcntl"] = MagicMock()
sys.modules["pwd"] = MagicMock()
sys.modules["syslog"] = MagicMock()
sys.modules["tty"] = MagicMock()

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

from plugins.modules.purefb_network import (  # noqa: E402
    _attached_server_name,
    create_iface,
    delete_iface,
    modify_iface,
)

MODULE_PATH = "plugins.modules.purefb_network"


def _module(**overrides):
    params = {
        "name": "vip1",
        "state": "present",
        "address": "10.0.0.10",
        "services": "data",
        "itype": "vip",
        "attached_server": None,
    }
    params.update(overrides)
    module = Mock()
    module.params = params
    module.check_mode = False
    module.fail_json = Mock(side_effect=SystemExit)
    module.exit_json = Mock(side_effect=SystemExit)
    return module


def _iface(address="10.0.0.10", attached="_array_server"):
    """An interface shaped like a GET result.

    ``spec`` matters: the real NetworkInterface raises AttributeError for
    anything outside its field set, and there is no singular
    ``attached_server`` field. Using a bare Mock here would hide that.
    """
    iface = Mock(spec=["name", "address", "services", "type", "attached_servers"])
    iface.name = "vip1"
    iface.address = address
    iface.services = ["data"]
    iface.type = "vip"
    if attached is None:
        iface.attached_servers = []
    else:
        ref = Mock(spec=["name"])
        ref.name = attached
        iface.attached_servers = [ref]
    return iface


def _blade(status_code=200):
    blade = Mock()
    response = Mock()
    response.status_code = status_code
    response.items = []
    for call in (
        "get_network_interfaces",
        "post_network_interfaces",
        "patch_network_interfaces",
        "delete_network_interfaces",
    ):
        getattr(blade, call).return_value = response
    return blade


class TestAttachedServerName:
    def test_reads_the_name_out_of_the_list(self):
        assert _attached_server_name(_iface(attached="fred")) == "fred"

    def test_none_when_no_server_attached(self):
        assert _attached_server_name(_iface(attached=None)) is None

    def test_none_when_the_field_is_absent(self):
        assert _attached_server_name(Mock(spec=["name"])) is None


class TestCreateIface:
    @patch(MODULE_PATH + ".NetworkInterface")
    def test_no_attached_server_leaves_the_field_unset(self, mock_ni):
        """A real model has no attached_servers until something sets it.

        SimpleNamespace is used rather than a Mock because hasattr() on a
        Mock is always true, which would hide the field never being set.
        """
        created = SimpleNamespace()
        mock_ni.return_value = created
        module = _module()
        blade = _blade()
        try:
            create_iface(module, blade)
        except SystemExit:
            pass
        assert not hasattr(created, "attached_servers")
        blade.post_network_interfaces.assert_called_once()
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".NetworkInterface")
    def test_attached_server_is_sent_as_a_list_of_references(self, mock_ni):
        """The API field is attached_servers, plural, and a list."""
        created = SimpleNamespace()
        mock_ni.return_value = created
        module = _module(attached_server="realm-1::server-1")
        blade = _blade()
        try:
            create_iface(module, blade)
        except SystemExit:
            pass
        assert created.attached_servers == [{"name": "realm-1::server-1"}]
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".NetworkInterface")
    def test_check_mode_makes_no_call(self, mock_ni):
        module = _module(attached_server="fred")
        module.check_mode = True
        blade = _blade()
        try:
            create_iface(module, blade)
        except SystemExit:
            pass
        blade.post_network_interfaces.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".get_error_message")
    @patch(MODULE_PATH + ".NetworkInterface")
    def test_fails_on_error_response(self, mock_ni, mock_gem):
        mock_gem.return_value = "boom"
        module = _module()
        blade = _blade(status_code=400)
        try:
            create_iface(module, blade)
        except SystemExit:
            pass
        module.fail_json.assert_called_once()
        assert "creation failed" in module.fail_json.call_args[1]["msg"]


class TestModifyIface:
    @patch(MODULE_PATH + ".get_iface")
    def test_no_change_when_nothing_differs(self, mock_get):
        mock_get.return_value = _iface()
        module = _module()
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        blade.patch_network_interfaces.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    @patch(MODULE_PATH + ".get_iface")
    def test_attached_server_already_correct_is_not_a_change(self, mock_get):
        """Re-running an unchanged task must not report changed."""
        mock_get.return_value = _iface(attached="fred")
        module = _module(attached_server="fred")
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        blade.patch_network_interfaces.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    @patch(MODULE_PATH + ".NetworkInterfacePatch")
    @patch(MODULE_PATH + ".get_iface")
    def test_moves_to_a_different_server(self, mock_get, mock_patch):
        mock_get.return_value = _iface(attached="_array_server")
        module = _module(attached_server="fred")
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        mock_patch.assert_called_once_with(attached_servers=[{"name": "fred"}])
        blade.patch_network_interfaces.assert_called_once()
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".NetworkInterfacePatch")
    @patch(MODULE_PATH + ".get_iface")
    def test_attaches_when_nothing_is_attached_yet(self, mock_get, mock_patch):
        mock_get.return_value = _iface(attached=None)
        module = _module(attached_server="fred")
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        mock_patch.assert_called_once_with(attached_servers=[{"name": "fred"}])
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".NetworkInterfacePatch")
    @patch(MODULE_PATH + ".get_iface")
    def test_address_change_alone(self, mock_get, mock_patch):
        mock_get.return_value = _iface(address="10.0.0.9")
        module = _module(address="10.0.0.10")
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        mock_patch.assert_called_once_with(address="10.0.0.10")
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".NetworkInterfacePatch")
    @patch(MODULE_PATH + ".get_iface")
    def test_address_and_server_both_change(self, mock_get, mock_patch):
        """The two are independent, not mutually exclusive."""
        mock_get.return_value = _iface(address="10.0.0.9", attached="_array_server")
        module = _module(address="10.0.0.10", attached_server="fred")
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        assert blade.patch_network_interfaces.call_count == 2
        assert mock_patch.call_args_list[0][1] == {"address": "10.0.0.10"}
        assert mock_patch.call_args_list[1][1] == {
            "attached_servers": [{"name": "fred"}]
        }
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".get_iface")
    def test_omitting_attached_server_never_detaches(self, mock_get):
        """An unset option must not strip the interface's current server."""
        mock_get.return_value = _iface(attached="fred")
        module = _module(attached_server=None)
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        blade.patch_network_interfaces.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    @patch(MODULE_PATH + ".NetworkInterfacePatch")
    @patch(MODULE_PATH + ".get_iface")
    def test_check_mode_reports_change_without_patching(self, mock_get, mock_patch):
        mock_get.return_value = _iface(attached="_array_server")
        module = _module(attached_server="fred")
        module.check_mode = True
        blade = _blade()
        try:
            modify_iface(module, blade)
        except SystemExit:
            pass
        blade.patch_network_interfaces.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)


class TestDeleteIface:
    def test_deletes(self):
        module = _module()
        blade = _blade()
        try:
            delete_iface(module, blade)
        except SystemExit:
            pass
        blade.delete_network_interfaces.assert_called_once_with(names=["vip1"])
        module.exit_json.assert_called_once_with(changed=True)

    def test_check_mode_makes_no_call(self):
        module = _module()
        module.check_mode = True
        blade = _blade()
        try:
            delete_iface(module, blade)
        except SystemExit:
            pass
        blade.delete_network_interfaces.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)
