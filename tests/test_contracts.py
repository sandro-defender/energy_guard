"""Contract tests: packaging, UI text and documentation stay in sync.

These tests exist for future maintainers (human or AI): they fail loudly when a
service is added without ``services.yaml``/``strings.json`` entries, when the
documented service list drifts from the code, or when the manifest stops being a
valid HACS/Home Assistant custom integration manifest.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from custom_components.energy_guard.const import DOMAIN, SERVICE_NAMES, VERSION
from custom_components.energy_guard.options_flow import MENU_OPTIONS
from custom_components.energy_guard.services import (
    CALIBRATE_SCHEMA,
    CLEAR_SCHEMA,
    EXPORT_TEMPLATES_SCHEMA,
    REPAIR_SCHEMA,
    REPORT_SCHEMA,
    SCAN_SCHEMA,
)

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
REPO_ROOT = COMPONENT_DIR.parents[1]

SCHEMAS = {
    "scan_statistics": SCAN_SCHEMA,
    "repair_statistics": REPAIR_SCHEMA,
    "calibrate_utility_meter": CALIBRATE_SCHEMA,
    "clear_statistics": CLEAR_SCHEMA,
    "export_repair_report": REPORT_SCHEMA,
    "export_templates": EXPORT_TEMPLATES_SCHEMA,
}

#: Services that change data must say so in ``services.yaml``.
DATA_CHANGING = {"repair_statistics", "clear_statistics", "calibrate_utility_meter"}
READ_ONLY = {"scan_statistics", "export_repair_report", "export_templates"}


@pytest.fixture(scope="module")
def services_yaml() -> dict:
    """Return the parsed services.yaml."""
    return yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text())


@pytest.fixture(scope="module")
def strings() -> dict:
    """Return the parsed strings.json."""
    return json.loads((COMPONENT_DIR / "strings.json").read_text())


def test_manifest_is_a_valid_custom_integration_manifest() -> None:
    """The manifest carries everything HACS and Home Assistant need."""
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())

    assert manifest["domain"] == DOMAIN
    assert manifest["name"] == "Energy Guard"
    assert manifest["version"] == VERSION
    assert manifest["config_flow"] is True
    assert manifest["single_config_entry"] is True
    assert manifest["integration_type"] == "service"
    assert manifest["iot_class"] == "calculated"
    assert manifest["codeowners"], "codeowners must not be empty"
    # No runtime dependencies: Energy Guard only uses Home Assistant APIs.
    assert manifest["requirements"] == []
    assert "loggers" not in manifest
    for key in ("documentation", "issue_tracker"):
        assert manifest[key].startswith("https://github.com/")
    # The recorder must not be a hard dependency (protection works without it).
    assert "recorder" in manifest["after_dependencies"]
    assert "recorder" not in manifest["dependencies"]


def test_hacs_metadata_points_at_the_component() -> None:
    """hacs.json describes an integration inside custom_components/."""
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text())

    assert hacs["render_readme"] is True
    assert hacs["content_in_root"] is False
    assert (COMPONENT_DIR / "manifest.json").exists()


def test_every_service_is_registered_documented_and_translated(
    services_yaml: dict, strings: dict
) -> None:
    """A service exists in code, in services.yaml and in strings.json."""
    assert set(SERVICE_NAMES) == set(SCHEMAS)
    assert set(services_yaml) == set(SERVICE_NAMES)
    assert set(strings["services"]) == set(SERVICE_NAMES)

    for service in DATA_CHANGING:
        assert services_yaml[service]["fields"]["confirm"], service
        assert "confirm" in strings["services"][service]["fields"]
    for service in READ_ONLY:
        assert "confirm" not in services_yaml[service]["fields"], service


def test_services_yaml_fields_match_the_schemas(services_yaml: dict) -> None:
    """Every parameter of a service is documented with the same name."""
    for service, schema in SCHEMAS.items():
        documented = set(services_yaml[service]["fields"])
        coded = set(schema.schema)
        assert coded <= documented, (
            f"{service}: undocumented parameters {coded - documented}"
        )
        assert documented <= coded | {"config_entry_id"}, (
            f"{service}: documented but not implemented {documented - coded}"
        )
        for name, field in services_yaml[service]["fields"].items():
            assert field.get("description"), f"{service}.{name} needs a description"


def test_options_menu_sections_are_all_translated(strings: dict) -> None:
    """Every options menu entry has a step with a title."""
    options_steps = strings["options"]["step"]
    for section in MENU_OPTIONS:
        assert section in options_steps, section
        assert options_steps[section]["title"]
    # The message/label sets the flows can raise must exist too.
    assert strings["config"]["error"]
    assert strings["options"]["error"]
    assert strings["options"]["abort"]
    for key in ("not_loaded", "yaml_written"):
        assert key in strings["options"]["abort"]


def test_issues_are_translated(strings: dict) -> None:
    """Repairs issues have titles/descriptions, including their placeholders."""
    from custom_components.energy_guard.repairs import (
        ISSUE_BLOCKED_READINGS,
        ISSUE_STATISTICS_OFFSET,
    )

    for key in (ISSUE_STATISTICS_OFFSET, ISSUE_BLOCKED_READINGS):
        issue = strings["issues"][key]
        assert issue["title"], key
        assert issue["description"], key
    issue = strings["issues"][ISSUE_STATISTICS_OFFSET]
    for placeholder in ("statistic_id", "offset", "unit", "start_time", "count"):
        marker = "{" + placeholder + "}"
        documented = marker in issue["description"] or marker in issue["title"]
        assert documented, f"statistics_offset does not mention {placeholder}"


def test_translations_match_strings(strings: dict) -> None:
    """translations/en.json is the English source of truth, not a stale copy."""
    translations = json.loads((COMPONENT_DIR / "translations" / "en.json").read_text())
    assert translations == strings


def test_readme_mentions_every_service_and_the_worked_example() -> None:
    """The README documents the public API, including the GEL example."""
    readme = (REPO_ROOT / "README.md").read_text()

    for service in SERVICE_NAMES:
        assert f"energy_guard.{service}" in readme, service
    for needed in (
        "38243.46",
        "0.235",
        "8987.21",
        "confirm_cost",
        "confirm: true",
        "HACS",
        "sensor.grid_import_protected",
        "sensor.dp2_phase_a",
        "utility_meter",
        "device_class: energy",
    ):
        assert needed in readme, f"README misses {needed!r}"


def test_documentation_files_exist_and_cross_reference() -> None:
    """The documentation set requested for v1.1 exists and links to each other."""
    docs = [
        "README.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "LICENSE",
        "docs/ARCHITECTURE.md",
        "docs/SAFETY.md",
        "docs/SERVICES.md",
        "docs/STATISTICS-REPAIR.md",
        "docs/DEVELOPMENT.md",
        "docs/CONFIGURATION.md",
        "docs/DASHBOARD.md",
    ]
    for relative in docs:
        path = REPO_ROOT / relative
        assert path.exists(), relative
        assert path.read_text().strip(), relative

    readme = (REPO_ROOT / "README.md").read_text()
    for relative in docs[4:]:
        assert relative in readme, relative


def test_no_direct_database_access_anywhere() -> None:
    """Energy Guard must never touch the recorder database directly."""
    forbidden = re.compile(
        r"sqlite3|create_engine|sessionmaker|execute\(|home-assistant_v2", re.IGNORECASE
    )
    for path in sorted(COMPONENT_DIR.glob("*.py")):
        text = path.read_text()
        assert not forbidden.search(text), f"{path.name} looks like direct DB access"
        assert "recorder.db" not in text


def test_no_secrets_or_tokens_in_the_component() -> None:
    """No name or literal in the code mentions credentials of any kind.

    Documentation (docstrings/comments) may *talk* about secrets - that is how
    users are told not to paste them - but no identifier or string used by the
    code may refer to a token, password or API key.
    """
    needles = ("api_key", "apikey", "access_token", "password", "passwd", "bearer")
    for path in sorted(COMPONENT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        for node in ast.walk(tree):
            values: list[str] = []
            if isinstance(node, ast.Name):
                values.append(node.id)
            elif isinstance(node, ast.Attribute):
                values.append(node.attr)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value not in docstrings
            ):
                values.append(node.value)
            for value in values:
                lowered = value.lower()
                for needle in needles:
                    assert needle not in lowered, (
                        f"{path.name} uses a credential-like name {value!r}"
                    )


def test_module_docstrings_explain_responsibility() -> None:
    """Every module documents its own responsibility."""
    for path in sorted(COMPONENT_DIR.glob("*.py")):
        doc = ast.get_docstring(ast.parse(path.read_text()))
        assert doc, f"{path.name} has no module docstring"
        assert len(doc.splitlines()) >= 2, f"{path.name} needs a real docstring"


def test_repository_urls_point_at_the_real_project() -> None:
    """Manifest, README and the placeholder guard agree on the repository."""
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())
    assert all(owner.startswith("@") for owner in manifest["codeowners"])
    repository = "https://github.com/sandro-defender/energy_guard"
    for key in ("documentation", "issue_tracker"):
        assert manifest[key].startswith(repository), key

    readme = (REPO_ROOT / "README.md").read_text()
    assert manifest["codeowners"][0] in readme
    assert repository in readme
    # No leftovers of the template a fork starts from.
    for relative in (
        "README.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "docs/DEVELOPMENT.md",
        "custom_components/energy_guard/manifest.json",
        "custom_components/energy_guard/repairs.py",
    ):
        text = (REPO_ROOT / relative).read_text()
        assert "your-github-username" not in text, relative
