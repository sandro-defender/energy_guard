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
import struct
import tomllib
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


#: The oldest Home Assistant the suite verifies. requirements_test_min.txt
#: pins pytest-homeassistant-custom-component 0.13.272, which ships
#: homeassistant 2025.8.3 (the last patch of the declared minimum release),
#: and the CI "verified minimum" job runs the whole suite against it. The
#: minimum is 2025.8 because options_flow.py subclasses OptionsFlowWithReload,
#: which Home Assistant added in 2025.8 (home-assistant/core#146910).
TESTED_MINIMUM_HOME_ASSISTANT = "2025.8.0"
MINIMUM_HARNESS_PIN = "pytest-homeassistant-custom-component==0.13.272"


def test_the_declared_home_assistant_minimum_is_the_tested_minimum() -> None:
    """hacs.json must never claim more compatibility than CI verifies.

    The `homeassistant` minimum is user-facing: HACS refuses to install or
    update below it. The claim is verified by running the full suite against
    the oldest supported release (requirements_test_min.txt plus the
    "declared minimum" job in tests.yml). Bumping either side means bumping
    the other - an untested compatibility claim is how one-star issues are
    born.
    """
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text())
    assert hacs["homeassistant"] == TESTED_MINIMUM_HOME_ASSISTANT

    requirements = (REPO_ROOT / "requirements_test_min.txt").read_text()
    assert MINIMUM_HARNESS_PIN in requirements

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    )
    includes = workflow["jobs"]["tests"]["strategy"]["matrix"]["include"]
    minimum_jobs = [entry for entry in includes if not entry["coverage"]]
    assert minimum_jobs, "CI must test the declared minimum Home Assistant"
    assert minimum_jobs[0]["requirements"] == "requirements_test_min.txt"

    # The README badge advertises the same minimum.
    readme = (REPO_ROOT / "README.md").read_text()
    major_minor = TESTED_MINIMUM_HOME_ASSISTANT.rsplit(".", 1)[0]
    assert f"Home_Assistant-{major_minor}+" in readme


def test_version_is_consistent_across_the_repository() -> None:
    """manifest.json, const.VERSION and pyproject.toml carry the same version.

    The manifest version is what HACS shows users, the git tag is what turns a
    commit into a HACS release, and pyproject is what development tooling sees.
    When they drift, users stop receiving updates - this test makes that loud.
    """
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

    assert manifest["version"] == VERSION
    assert pyproject["project"]["version"] == VERSION


def test_changelog_documents_the_current_version() -> None:
    """The CHANGELOG has a section for the released version.

    Keep a Changelog style: `## [X.Y.Z] - date`. If this fails after a version
    bump, either the changelog is missing the release section or VERSION was
    bumped by mistake.
    """
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
    pattern = rf"^## \[{re.escape(VERSION)}\]"
    assert re.search(pattern, changelog, re.MULTILINE), (
        f"CHANGELOG.md has no '## [{VERSION}]' section; bumping the version "
        "requires a changelog entry."
    )


def test_release_workflow_gates_and_publishes_tag_pushes() -> None:
    """The tag-push workflow only releases verified, documented versions.

    Pushing a `1.*`/`2.*` tag must (1) re-run ruff and the full test suite on
    the tagged commit, (2) refuse to publish unless the tag equals
    ``manifest.json``/``const.VERSION`` and the CHANGELOG documents it, and
    (3) publish the release with the CHANGELOG section as its notes. This is
    what makes a tag the source of truth for HACS users, so the contract is
    pinned here instead of living only in the workflow's comments.
    """
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    )
    assert workflow["name"] == "Release"

    # The repository tags without a `v` prefix (`1.0.0`, ...): the trigger must
    # match bare semver-ish tags, and only those. (YAML parses the workflow's
    # `on:` key as the boolean True, hence the lookup below.)
    tags = workflow[True]["push"]["tags"]
    assert "1.*" in tags
    assert "2.*" in tags

    steps = workflow["jobs"]["gates"]["steps"]
    runs = " \n".join(step.get("run", "") for step in steps)

    # Quality gates run on the tagged commit before anything is published.
    assert "ruff check ." in runs
    assert "ruff format --check ." in runs
    assert "pytest tests/" in runs

    # Release gates: tag == manifest version == const.VERSION, and the
    # CHANGELOG must document the tag. Both failures exit non-zero (no release).
    assert "manifest.json" in runs
    assert "const" in runs
    assert "CHANGELOG.md" in runs
    assert "gh release create" in runs
    assert "--notes-file release_notes.md" in runs

    # The notes are carved out of the CHANGELOG, not written free-hand.
    assert "awk" in runs
    assert "release_notes.md" in runs


def test_ci_measures_coverage_and_publishes_it() -> None:
    """The CI test job runs under coverage and shows the result.

    A coverage number that is never measured drifts silently; a data-safety
    tool should show its proof. The suite must run with ``--cov``, produce a
    machine-readable report, publish the total in the job summary, and pin
    ``pytest-cov`` in the test requirements so the measurement cannot vanish.
    """
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    )
    steps = workflow["jobs"]["tests"]["steps"]
    runs = " \n".join(step.get("run", "") for step in steps)

    assert "--cov" in runs
    assert "--cov-report=json" in runs
    assert "GITHUB_STEP_SUMMARY" in runs

    requirements = (REPO_ROOT / "requirements_test.txt").read_text()
    assert "pytest-cov" in requirements

    # The floor is declared once, in pyproject.toml, where pytest-cov reads it.
    # The pinned suite measured 90% on 2026-09-21; 89 (one point of headroom)
    # is a one-way ratchet and must never be lowered.
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    floor = pyproject["tool"]["coverage"]["report"]["fail_under"]
    assert floor >= 89, "the coverage floor must never be lowered"


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


def test_every_github_link_points_at_the_real_project() -> None:
    """No file links to any repository but the real one (regression test).

    The device page once opened a wrong-cased placeholder repository because
    ``hub.py`` carried its own copy of the URL.  Every link is derived from
    ``const.REPOSITORY_URL`` now; this sweep fails on any other owner/name.

    Exception: infrastructure/tool repositories referenced by CI and lint
    configuration (GitHub Actions, pre-commit hooks).  Those are tool links,
    not project links - anything added here must be developer tooling, never
    documentation or a user-facing link.
    """
    from custom_components.energy_guard.const import REPOSITORY_URL

    link = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
    owner, _, name = REPOSITORY_URL.partition("https://github.com/")[2].partition("/")
    assert owner, REPOSITORY_URL
    assert name, REPOSITORY_URL
    tool_repos = {
        ("actions", "checkout"),
        ("astral-sh", "ruff-pre-commit"),
        ("astral-sh", "setup-uv"),
        ("hacs", "action"),
        ("home-assistant", "actions"),
    }
    skipped_dirs = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
    }
    checked = 0
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in {
            ".py",
            ".json",
            ".md",
            ".yaml",
            ".yml",
            ".js",
            ".toml",
        }:
            continue
        if skipped_dirs & set(path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if path != Path(__file__):  # this test names the placeholder itself
            assert "your-github-username" not in text, path
        for found_owner, found_name in link.findall(text):
            checked += 1
            found_name = found_name.removesuffix(".git")  # clone URLs
            assert (found_owner, found_name) == (owner, name) or (
                found_owner,
                found_name,
            ) in tool_repos, f"{path}: github.com/{found_owner}/{found_name}"
    assert checked, "the sweep should see at least one repository link"


def test_every_form_field_is_labelled_and_described(strings: dict) -> None:
    """Each schema key has a label and a 'what is it' description.

    This is the check ``selectors.py`` promises: a form field without a label
    shows its raw key in Home Assistant, and one without a description leaves
    the user guessing.  The edit forms mirror their add forms exactly.
    """
    from custom_components.energy_guard.selectors import (
        backups_schema,
        cost_schema,
        derived_schema,
        detection_schema,
        protected_schema,
        statistics_schema,
        utility_meter_schema,
    )

    config_steps = strings["config"]["step"]
    options_steps = strings["options"]["step"]
    mapping = {
        protected_schema: ("protected_sensors_add", "protected_sensors_edit_form"),
        derived_schema: ("derived_sensors_add", "derived_sensors_edit_form"),
        utility_meter_schema: ("utility_meters_add", "utility_meters_edit_form"),
        detection_schema: ("detection_rules",),
        statistics_schema: ("statistics_repair",),
        cost_schema: ("cost_repair",),
        backups_schema: ("backups_reports",),
    }
    for schema_fn, steps in mapping.items():
        keys = {str(marker) for marker in schema_fn().schema}
        for step in steps:
            node = options_steps[step]
            assert keys <= set(node.get("data", {})), f"{step} misses a label"
            assert keys <= set(node.get("data_description", {})), (
                f"{step} misses a description"
            )
    # The first-run detection step uses the same schema, so it needs the same
    # texts.
    detection_keys = {str(marker) for marker in detection_schema().schema}
    node = config_steps["detection"]
    assert detection_keys <= set(node.get("data", {})), "detection misses a label"
    assert detection_keys <= set(node.get("data_description", {})), (
        "detection misses a description"
    )
    # Edit forms never drift from their add forms.
    for add, edit in (
        ("protected_sensors_add", "protected_sensors_edit_form"),
        ("derived_sensors_add", "derived_sensors_edit_form"),
        ("utility_meters_add", "utility_meters_edit_form"),
    ):
        assert options_steps[edit]["data"] == options_steps[add]["data"], edit
        assert (
            options_steps[edit]["data_description"]
            == options_steps[add]["data_description"]
        ), edit


def test_selector_option_labels_cover_the_dropdowns(strings: dict) -> None:
    """The translated dropdown options match the selectors that use them."""
    from custom_components.energy_guard.const import DERIVED_MODES, SCAN_SCOPES
    from custom_components.energy_guard.selectors import MODE_OPTIONS

    assert set(strings["selector"]["scan_scope"]["options"]) == set(SCAN_SCOPES)
    assert set(strings["selector"]["mode"]["options"]) == set(DERIVED_MODES)
    assert {option["value"] for option in MODE_OPTIONS} == set(DERIVED_MODES)
    for group in ("scan_scope", "mode"):
        for value, label in strings["selector"][group]["options"].items():
            assert label, (group, value)


def test_service_icons_cover_every_service() -> None:
    """icons.json gives every service an icon for the automation editors.

    Entity icons stay where they are (device classes and the state-dependent
    ``_attr_icon`` properties, which are still supported); the icons file only
    needs the services, which have no other way to get one.
    """
    from custom_components.energy_guard.const import SERVICE_NAMES

    icons = json.loads((COMPONENT_DIR / "icons.json").read_text())
    assert set(icons["services"]) == set(SERVICE_NAMES)
    for service, icon in icons["services"].items():
        assert re.fullmatch(r"mdi:[a-z0-9-]+", icon), (service, icon)


def test_brand_images_are_valid_pngs() -> None:
    """The brand/ folder carries what Home Assistant 2026.3+ and HACS read.

    Local brand images take priority over the brands CDN; ``icon.png`` alone
    is enough for the logo to appear, the ``@2x`` variants keep retina
    displays crisp.  No dark variants: the artwork has a solid background, so
    it works on light and dark themes as-is.
    """
    brand = COMPONENT_DIR / "brand"
    expected = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "logo.png": (600, 200),
        "logo@2x.png": (1200, 400),
    }
    for name, size in expected.items():
        data = (brand / name).read_bytes()
        assert len(data) > 1024, f"{name} looks like a placeholder"
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == size, f"{name} is {width}x{height}"


def test_brand_artwork_matches_across_densities() -> None:
    """icon.png is the 512 artwork downscaled, with transparent corners.

    Home Assistant picks one or the other by display density, so a different
    framing would visibly change the logo between screens.
    """
    pytest.importorskip("PIL")
    from PIL import Image

    brand = COMPONENT_DIR / "brand"
    small = Image.open(brand / "icon.png").convert("RGBA")
    big = (
        Image.open(brand / "icon@2x.png")
        .convert("RGBA")
        .resize((256, 256), Image.LANCZOS)
    )
    assert small.size == big.size == (256, 256)
    assert list(small.getdata()) == list(big.getdata())
    assert small.getpixel((0, 0))[3] == 0, "icon corners must be transparent"
