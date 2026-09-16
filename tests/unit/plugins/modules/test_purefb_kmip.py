# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the purefb_kmip module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import sys
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

from plugins.modules.purefb_kmip import (  # noqa: E402
    create_kmip,
    delete_kmip,
    get_kmip,
    report_kmip_test,
    update_kmip,
)

MODULE_PATH = "plugins.modules.purefb_kmip"


def _params(**overrides):
    params = {
        "name": "kmip1",
        "state": "present",
        "ca_certificate": None,
        "ca_certificate_group": None,
        "uris": None,
    }
    params.update(overrides)
    return params


def _module(**overrides):
    module = Mock()
    module.params = _params(**overrides)
    module.check_mode = False
    module.fail_json = Mock(side_effect=SystemExit)
    module.exit_json = Mock(side_effect=SystemExit)
    return module


def _reference(name):
    ref = Mock(spec=["name"])
    ref.name = name
    return ref


def _current(uris=None, ca_certificate="ca1", ca_certificate_group=None):
    """A KMIP object shaped like a GET result.

    ``spec`` matters: the real KmipServer raises AttributeError for anything
    outside its field set, which is how the previous implementation broke.
    """
    obj = Mock(spec=["name", "uris", "ca_certificate", "ca_certificate_group", "id"])
    obj.name = "kmip1"
    obj.uris = uris if uris is not None else ["1.1.1.1:5696"]
    obj.ca_certificate = _reference(ca_certificate) if ca_certificate else None
    obj.ca_certificate_group = (
        _reference(ca_certificate_group) if ca_certificate_group else None
    )
    return obj


def _blade(status_code=200, items=None):
    blade = Mock()
    response = Mock()
    response.status_code = status_code
    response.items = items if items is not None else []
    for call in (
        "get_kmip",
        "post_kmip",
        "patch_kmip",
        "delete_kmip",
        "get_certificates",
        "get_certificate_groups",
        "get_kmip_test",
    ):
        getattr(blade, call).return_value = response
    return blade


class TestGetKmip:
    """get_kmip must tolerate the absent-object response."""

    def test_returns_object_when_present(self):
        current = _current()
        blade = _blade(items=[current])
        assert get_kmip(_module(), blade) is current

    def test_returns_none_on_error_response(self):
        """An absent object comes back as a non-200 with no items."""
        blade = _blade(status_code=400)
        assert get_kmip(_module(), blade) is None

    def test_returns_none_on_empty_items(self):
        blade = _blade(items=[])
        assert get_kmip(_module(), blade) is None


class TestCreateKmip:
    """Create requires uris and one CA, and sends references."""

    def test_requires_uris(self):
        module = _module(ca_certificate="ca1")
        try:
            create_kmip(module, _blade())
        except SystemExit:
            pass
        module.fail_json.assert_called_once()
        assert "uris is required" in module.fail_json.call_args[1]["msg"]

    def test_requires_a_ca(self):
        """The array rejects a KMIP object with neither CA field."""
        module = _module(uris=["1.1.1.1:5696"])
        try:
            create_kmip(module, _blade())
        except SystemExit:
            pass
        module.fail_json.assert_called_once()
        msg = module.fail_json.call_args[1]["msg"]
        assert "ca_certificate or ca_certificate_group" in msg

    @patch(MODULE_PATH + ".Reference")
    @patch(MODULE_PATH + ".KmipServer")
    def test_sends_sorted_uris_and_ca_reference(self, mock_kmip, mock_reference):
        module = _module(uris=["2.2.2.2:5696", "1.1.1.1:5696"], ca_certificate="ca1")
        blade = _blade()
        try:
            create_kmip(module, blade)
        except SystemExit:
            pass
        mock_reference.assert_called_once_with(name="ca1")
        mock_kmip.assert_called_once_with(
            uris=["1.1.1.1:5696", "2.2.2.2:5696"],
            ca_certificate=mock_reference.return_value,
        )
        blade.post_kmip.assert_called_once()
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".Reference")
    @patch(MODULE_PATH + ".KmipServer")
    def test_uses_certificate_group_when_given(self, mock_kmip, mock_reference):
        module = _module(uris=["1.1.1.1:5696"], ca_certificate_group="grp1")
        try:
            create_kmip(module, _blade())
        except SystemExit:
            pass
        mock_reference.assert_called_once_with(name="grp1")
        assert "ca_certificate_group" in mock_kmip.call_args[1]

    def test_fails_when_named_certificate_is_absent(self):
        module = _module(uris=["1.1.1.1:5696"], ca_certificate="nope")
        blade = _blade()
        missing = Mock()
        missing.status_code = 400
        blade.get_certificates.return_value = missing
        try:
            create_kmip(module, blade)
        except SystemExit:
            pass
        module.fail_json.assert_called_once()
        assert "Certificate nope does not exist" in module.fail_json.call_args[1]["msg"]

    @patch(MODULE_PATH + ".KmipServer")
    def test_check_mode_makes_no_call(self, mock_kmip):
        module = _module(uris=["1.1.1.1:5696"], ca_certificate="ca1")
        module.check_mode = True
        blade = _blade()
        try:
            create_kmip(module, blade)
        except SystemExit:
            pass
        blade.post_kmip.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)


class TestUpdateKmip:
    """Update must diff against the real fields and PATCH only what differs."""

    def test_no_change_when_everything_matches(self):
        module = _module(uris=["1.1.1.1:5696"], ca_certificate="ca1")
        blade = _blade()
        try:
            update_kmip(module, blade, _current())
        except SystemExit:
            pass
        blade.patch_kmip.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_no_change_when_nothing_supplied(self):
        module = _module()
        blade = _blade()
        try:
            update_kmip(module, blade, _current())
        except SystemExit:
            pass
        blade.patch_kmip.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_uri_order_does_not_count_as_a_change(self):
        module = _module(uris=["2.2.2.2:5696", "1.1.1.1:5696"])
        blade = _blade()
        current = _current(uris=["1.1.1.1:5696", "2.2.2.2:5696"])
        try:
            update_kmip(module, blade, current)
        except SystemExit:
            pass
        blade.patch_kmip.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    @patch(MODULE_PATH + ".KmipServer")
    def test_patches_only_the_changed_field(self, mock_kmip):
        """A uris-only change must not resend the CA reference."""
        module = _module(uris=["9.9.9.9:5696"], ca_certificate="ca1")
        blade = _blade()
        try:
            update_kmip(module, blade, _current())
        except SystemExit:
            pass
        mock_kmip.assert_called_once_with(uris=["9.9.9.9:5696"])
        blade.patch_kmip.assert_called_once()
        module.exit_json.assert_called_once_with(changed=True)

    @patch(MODULE_PATH + ".Reference")
    @patch(MODULE_PATH + ".KmipServer")
    def test_patches_ca_certificate_change(self, mock_kmip, mock_reference):
        module = _module(ca_certificate="ca2")
        blade = _blade()
        try:
            update_kmip(module, blade, _current(ca_certificate="ca1"))
        except SystemExit:
            pass
        mock_reference.assert_called_once_with(name="ca2")
        mock_kmip.assert_called_once_with(ca_certificate=mock_reference.return_value)
        module.exit_json.assert_called_once_with(changed=True)

    def test_handles_absent_ca_reference_on_current(self):
        """A KMIP object may carry a group instead of a certificate."""
        module = _module(ca_certificate_group="grp1")
        blade = _blade()
        current = _current(ca_certificate=None, ca_certificate_group="grp1")
        try:
            update_kmip(module, blade, current)
        except SystemExit:
            pass
        blade.patch_kmip.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    @patch(MODULE_PATH + ".KmipServer")
    def test_check_mode_reports_change_without_patching(self, mock_kmip):
        module = _module(uris=["9.9.9.9:5696"])
        module.check_mode = True
        blade = _blade()
        try:
            update_kmip(module, blade, _current())
        except SystemExit:
            pass
        blade.patch_kmip.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)


class TestDeleteKmip:
    def test_deletes(self):
        module = _module()
        blade = _blade()
        try:
            delete_kmip(module, blade)
        except SystemExit:
            pass
        blade.delete_kmip.assert_called_once_with(names=["kmip1"])
        module.exit_json.assert_called_once_with(changed=True)

    def test_check_mode_makes_no_call(self):
        module = _module()
        module.check_mode = True
        blade = _blade()
        try:
            delete_kmip(module, blade)
        except SystemExit:
            pass
        blade.delete_kmip.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)


class TestTestKmip:
    def test_reports_components_without_claiming_a_change(self):
        """state=test is read-only, so it must not report changed."""
        component = Mock(
            spec=[
                "component_address",
                "component_name",
                "description",
                "destination",
                "enabled",
                "result_details",
                "success",
                "test_type",
                "resource",
            ]
        )
        component.component_address = "1.1.1.1"
        component.component_name = "fm1"
        component.description = "KMIP server connection"
        component.destination = "1.1.1.1:5696"
        component.enabled = True
        component.result_details = "detail"
        component.success = False
        component.test_type = "connectivity"
        component.resource = _reference("kmip1")

        module = _module(state="test")
        blade = _blade(items=[component])
        try:
            report_kmip_test(module, blade)
        except SystemExit:
            pass
        module.exit_json.assert_called_once()
        kwargs = module.exit_json.call_args[1]
        assert kwargs["changed"] is False
        assert kwargs["test_response"][0]["component_name"] == "fm1"
        assert kwargs["test_response"][0]["success"] is False
        assert kwargs["test_response"][0]["resource_name"] == "kmip1"
