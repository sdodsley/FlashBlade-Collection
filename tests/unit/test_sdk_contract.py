# -*- coding: utf-8 -*-

# Copyright: (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Contract tests for plugins/modules against the real SDK and AnsibleModule.

Why this file exists
--------------------
Every per-module unit test replaces ``pypureclient``, ``pypureclient.flashblade``
and this collection's ``module_utils`` packages with ``MagicMock`` so the suite
can run without a FlashBlade::

    sys.modules["pypureclient.flashblade"] = MagicMock()

A ``MagicMock`` accepts any keyword argument and any attribute, so SDK field
names and method signatures cannot be validated there. Both of these pass in a
mocked test and are wrong against the real objects::

    NetworkInterface(attached_server=...)          # field is attached_servers
    module.deprecate(part_one, part_two, version=) # binds part_two to version

``ansible-test sanity`` does not check either, so mistakes of this class reach
users as silently dropped fields or a runtime ``TypeError``. These tests close
the gap by introspecting the real objects.

Running in a subprocess
-----------------------
Those ``sys.modules`` assignments run at import time and leak across the whole
pytest process, so this audit cannot run in-process -- it would introspect the
same ``MagicMock``s. It runs in a clean interpreter instead and reports JSON.
This file doubles as that script; run it directly for the raw report::

    python tests/unit/test_sdk_contract.py
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import ast
import glob
import inspect
import json
import os
import subprocess
import sys

import pytest

COLLECTION_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
MODULE_GLOB = os.path.join(COLLECTION_ROOT, "plugins", "modules", "*.py")
MODULE_UTILS = ("common", "purefb", "version", "time_utils")

# AnsibleModule methods with a real, checkable signature. fail_json and
# exit_json take **kwargs, so they are permissive by design and are listed
# only so a stray extra positional argument is still caught.
CHECKED_METHODS = (
    "deprecate",
    "warn",
    "exit_json",
    "fail_json",
    "log",
    "boolean",
    "run_command",
    "atomic_move",
)

# Known, accepted mismatches. Each entry is (module, model, keyword).
#
# These exist on master today and are recorded here so this test can be
# introduced without turning CI red. Remove an entry in the same change that
# fixes it -- test_baseline_has_no_stale_entries fails if an entry no longer
# corresponds to a real finding, so the baseline cannot rot.
#
#   purefb_bucket / BucketAccessPolicyPost / name
#       BucketAccessPolicyPost has only 'rules'. The POST body is empty, but
#       the policy is addressed by bucket_names and its rule is created
#       separately, so the feature still works and 'name' is merely dead.
#   purefb_policy / SmbClientPolicyRule / access
#       SmbClientPolicyRule has no 'access'; the real field is 'permission',
#       which these calls already pass. 'access' is NFS export-policy
#       terminology and is inert here.
KNOWN_SDK_KWARG_MISMATCHES = frozenset(
    [
        ("purefb_bucket.py", "BucketAccessPolicyPost", "name"),
        ("purefb_policy.py", "SmbClientPolicyRule", "access"),
    ]
)


def _model_fields(cls):
    """Field names of an SDK model, across pydantic and swagger generations."""
    fields = getattr(cls, "model_fields", None) or getattr(cls, "__fields__", None)
    if fields:
        return set(fields)
    types = getattr(cls, "swagger_types", None) or getattr(cls, "openapi_types", None)
    return set(types or ())


def _literal(node):
    """Literal value of an AST node, or a unique sentinel when not literal."""
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001 - any non-literal is fine, we only need a value
        return object()


def _imported_sdk_names(tree):
    """Names this module imports from pypureclient."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "pypureclient"
        ):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _check_sdk_model_kwargs(tree, basename, sdk):
    """Model constructor keywords that do not exist on the real model."""
    findings = []
    imported = _imported_sdk_names(tree)
    if not imported:
        return findings
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in imported:
            continue
        cls = getattr(sdk, node.func.id, None)
        if cls is None or not inspect.isclass(cls):
            continue
        fields = _model_fields(cls)
        if not fields:
            continue
        for keyword in node.keywords:
            if keyword.arg is not None and keyword.arg not in fields:
                findings.append(
                    {
                        "module": basename,
                        "line": node.lineno,
                        "model": node.func.id,
                        "keyword": keyword.arg,
                        "real_fields": sorted(fields),
                    }
                )
    return findings


def _check_sdk_model_attributes(tree, basename, sdk):
    """Attributes assigned on an SDK model instance that the model lacks.

    Catches the ``obj.attached_server = ...`` shape, where the model field is
    ``attached_servers``. Assignment on a real model raises; on a MagicMock it
    silently succeeds and the value never reaches the wire.
    """
    findings = []
    seen = set()
    imported = _imported_sdk_names(tree)
    if not imported:
        return findings
    # Module and each function are both walked, and walking Module descends
    # into the functions, so the same assignment is reachable twice. Scoping
    # keeps variable resolution local; `seen` keeps the report unique.
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        local_models = {}
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Name) and call.func.id in imported:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            local_models[target.id] = call.func.id
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                ):
                    continue
                model_name = local_models.get(target.value.id)
                if model_name is None:
                    continue
                cls = getattr(sdk, model_name, None)
                if cls is None or not inspect.isclass(cls):
                    continue
                fields = _model_fields(cls)
                if not fields or target.attr in fields:
                    continue
                key = (basename, node.lineno, model_name, target.attr)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "module": basename,
                        "line": node.lineno,
                        "model": model_name,
                        "attribute": target.attr,
                        "real_fields": sorted(fields),
                    }
                )
    return findings


def _check_ansible_module_calls(tree, basename, signatures):
    """AnsibleModule method calls that will not bind to the real signature."""
    findings = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        signature = signatures.get(node.func.attr)
        if signature is None:
            continue
        base = node.func.value
        if not (isinstance(base, ast.Name) and base.id in ("module", "self")):
            continue
        if any(keyword.arg is None for keyword in node.keywords):
            continue
        args = [_literal(arg) for arg in node.args]
        kwargs = {keyword.arg: _literal(keyword.value) for keyword in node.keywords}
        try:
            signature.bind(object(), *args, **kwargs)
        except TypeError as exc:
            findings.append(
                {
                    "module": basename,
                    "line": node.lineno,
                    "method": node.func.attr,
                    "positional": len(node.args),
                    "keywords": sorted(kwargs),
                    "error": str(exc),
                }
            )
    return findings


def _helper_signatures():
    """Signatures of module_utils helpers, read with AST.

    The helpers import ``ansible_collections`` paths that are unavailable when
    this collection is not installed as a collection, so they are parsed rather
    than imported.
    """
    helpers = {}
    for name in MODULE_UTILS:
        path = os.path.join(COLLECTION_ROOT, "plugins", "module_utils", "%s.py" % name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                args = node.args
                positional = [a.arg for a in args.posonlyargs] + [
                    a.arg for a in args.args
                ]
                helpers[node.name] = {
                    "positional": positional,
                    "defaults": len(args.defaults),
                    "vararg": args.vararg is not None,
                    "kwarg": args.kwarg is not None,
                    "kwonly": [a.arg for a in args.kwonlyargs],
                }
    return helpers


def _check_helper_calls(tree, basename, helpers):
    """module_utils helper calls with the wrong arity or unknown keywords."""
    findings = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        helper = helpers.get(node.func.id)
        if helper is None:
            continue
        if any(keyword.arg is None for keyword in node.keywords):
            continue
        keywords = [keyword.arg for keyword in node.keywords]
        positional = len(node.args)
        maximum = len(helper["positional"])
        minimum = maximum - helper["defaults"]
        supplied = positional + len([k for k in keywords if k in helper["positional"]])
        problem = None
        if positional > maximum and not helper["vararg"]:
            problem = "too many positional arguments: %d, maximum %d" % (
                positional,
                maximum,
            )
        elif supplied < minimum:
            problem = "too few arguments: %d supplied, %d required" % (
                supplied,
                minimum,
            )
        else:
            unknown = [
                k
                for k in keywords
                if k not in helper["positional"] and k not in helper["kwonly"]
            ]
            if unknown and not helper["kwarg"]:
                problem = "unknown keyword arguments: %s" % ", ".join(sorted(unknown))
        if problem:
            findings.append(
                {
                    "module": basename,
                    "line": node.lineno,
                    "helper": node.func.id,
                    "error": problem,
                }
            )
    return findings


def audit():
    """Run every contract check and return a JSON-serialisable report."""
    try:
        import pypureclient.flashblade as sdk
    except ImportError as exc:
        return {"skipped": "py-pure-client is not installed: %s" % exc}
    if type(sdk).__name__ == "MagicMock":
        return {"skipped": "pypureclient is mocked in this interpreter"}

    from ansible.module_utils.basic import AnsibleModule

    signatures = {}
    for name in CHECKED_METHODS:
        method = getattr(AnsibleModule, name, None)
        if method is not None:
            signatures[name] = inspect.signature(method)
    helpers = _helper_signatures()

    report = {
        "sdk_kwargs": [],
        "sdk_attributes": [],
        "module_methods": [],
        "helpers": [],
        "modules_scanned": 0,
    }
    for path in sorted(glob.glob(MODULE_GLOB)):
        basename = os.path.basename(path)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            report["module_methods"].append(
                {
                    "module": basename,
                    "line": exc.lineno or 0,
                    "method": "-",
                    "error": "syntax error: %s" % exc,
                }
            )
            continue
        report["modules_scanned"] += 1
        report["sdk_kwargs"].extend(_check_sdk_model_kwargs(tree, basename, sdk))
        report["sdk_attributes"].extend(
            _check_sdk_model_attributes(tree, basename, sdk)
        )
        report["module_methods"].extend(
            _check_ansible_module_calls(tree, basename, signatures)
        )
        report["helpers"].extend(_check_helper_calls(tree, basename, helpers))
    return report


def _run_audit():
    """Run audit() in a clean interpreter, immune to sys.modules stubbing."""
    result = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        capture_output=True,
        text=True,
        cwd=COLLECTION_ROOT,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "contract audit subprocess failed:\n%s\n%s" % (result.stdout, result.stderr)
        )
    try:
        return json.loads(result.stdout)
    except ValueError:
        pytest.fail(
            "contract audit produced no JSON:\n%s\n%s" % (result.stdout, result.stderr)
        )


@pytest.fixture(scope="module")
def report():
    data = _run_audit()
    if "skipped" in data:
        pytest.skip(data["skipped"])
    return data


def _format(findings, *keys):
    lines = []
    for finding in findings:
        detail = "  ".join("%s=%s" % (key, finding.get(key)) for key in keys)
        lines.append("  %s:%s  %s" % (finding["module"], finding["line"], detail))
    return "\n".join(lines)


def test_modules_were_scanned(report):
    """Guard against the audit silently finding nothing to look at."""
    assert report["modules_scanned"] > 0


def test_sdk_model_keywords_exist(report):
    """Every SDK model keyword must name a real field on that model.

    An unknown keyword is accepted by the model and then dropped, so the value
    never reaches the array and the task still reports success.
    """
    unexpected = [
        finding
        for finding in report["sdk_kwargs"]
        if (finding["module"], finding["model"], finding["keyword"])
        not in KNOWN_SDK_KWARG_MISMATCHES
    ]
    assert (
        not unexpected
    ), "SDK model keywords that do not exist on the real model:\n%s" % _format(
        unexpected, "model", "keyword", "real_fields"
    )


def test_sdk_model_attributes_exist(report):
    """Attributes assigned on an SDK model instance must be real fields."""
    assert not report[
        "sdk_attributes"
    ], "attributes assigned that the real model does not define:\n%s" % _format(
        report["sdk_attributes"], "model", "attribute", "real_fields"
    )


def test_ansible_module_calls_bind(report):
    """AnsibleModule method calls must bind to the real signatures.

    Two adjacent strings separated by a comma become two positional arguments,
    which binds the second to ``version`` and raises at runtime.
    """
    assert not report[
        "module_methods"
    ], "AnsibleModule calls that will raise TypeError:\n%s" % _format(
        report["module_methods"], "method", "positional", "keywords", "error"
    )


def test_module_utils_helper_calls_are_wellformed(report):
    """module_utils helper calls must match the helper definitions."""
    assert not report["helpers"], "malformed module_utils helper calls:\n%s" % _format(
        report["helpers"], "helper", "error"
    )


def test_baseline_has_no_stale_entries(report):
    """Every baseline entry must still correspond to a real finding.

    Keeps KNOWN_SDK_KWARG_MISMATCHES honest: when a mismatch is fixed its entry
    must be removed in the same change.
    """
    seen = {
        (finding["module"], finding["model"], finding["keyword"])
        for finding in report["sdk_kwargs"]
    }
    stale = sorted(KNOWN_SDK_KWARG_MISMATCHES - seen)
    assert not stale, (
        "baseline entries no longer found -- remove them from KNOWN_SDK_KWARG_MISMATCHES:\n%s"
        % "\n".join("  %s / %s / %s" % entry for entry in stale)
    )


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
