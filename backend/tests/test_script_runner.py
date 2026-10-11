"""Tests for the TW3 script runner."""

import time
from pathlib import Path

import pytest

from backend.games.total_war_warhammer_3 import script_runner as sr
from backend.games.total_war_warhammer_3.script_runner import (
    SCRIPT_REGISTRY,
    PreflightError,
    RunInProgressError,
    ScriptDef,
    UnknownScriptError,
    _TEST_SCRIPT_REGISTRY,
    _log,
    _preflight,
    cancel_run,
    current_run,
    start_run,
)

FIXTURES = Path(__file__).parent / "fixtures" / "helper_scripts"


def test_script_registry_has_expected_entries():
    assert set(SCRIPT_REGISTRY.keys()) == {
        "update_dynamic_rors",
        "update_dynamic_rors_vanilla",
        "update_double_unit_size",
        "update_modified_attribute_mods",
        "process_main_units_tables",
        "glf_inner_join",
        "update_ttc_compat",
        "update",
    }


def test_script_def_for_vanilla_mode():
    sd = SCRIPT_REGISTRY["update_dynamic_rors_vanilla"]
    assert isinstance(sd, ScriptDef)
    assert sd.module == "generators.update_dynamic_rors"
    assert sd.args == ["--reset", "--vanilla"]


def test_registry_launches_scripts_from_the_reorganized_layout():
    """Generators and tools run as modules from the helper_scripts root. `update.py` still runs as a file."""
    assert {script_id: sd.argv() for script_id, sd in SCRIPT_REGISTRY.items()} == {
        "update_dynamic_rors": ["-m", "generators.update_dynamic_rors"],
        "update_dynamic_rors_vanilla": ["-m", "generators.update_dynamic_rors"],
        "update_double_unit_size": ["-m", "generators.update_double_unit_size"],
        "update_modified_attribute_mods": ["-m", "generators.update_modified_attribute_mods"],
        "process_main_units_tables": ["-m", "generators.process_main_units_tables"],
        "glf_inner_join": ["-m", "tools.glf_inner_join"],
        "update_ttc_compat": ["-m", "generators.update_ttc_compat"],
        "update": ["update.py"],
    }


def _settings(helper=FIXTURES, rpfm=None, drive="F:"):
    return {
        "helper_scripts_path": str(helper) if helper else "",
        "rpfm_cli_path": str(rpfm) if rpfm else "",
        "steam_library_drive": drive,
    }


def test_preflight_passes_with_valid_settings():
    _preflight(_settings())  # does not raise


def test_preflight_fails_when_helper_scripts_unset():
    with pytest.raises(PreflightError) as exc:
        _preflight(_settings(helper=None))
    assert "helper_scripts_path" in exc.value.missing


def test_preflight_fails_when_rpfm_missing(tmp_path):
    # Create a directory without rpfm_cli.exe.
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "schema_wh3.ron").write_text("")
    (tmp_path / "schemas" / "schema_wh3.json").write_text("{}")
    with pytest.raises(PreflightError) as exc:
        _preflight(_settings(helper=tmp_path))
    assert any("rpfm_cli" in m for m in exc.value.missing)


def test_preflight_passes_without_generated_schema_json(tmp_path):
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "schema_wh3.ron").write_text("")
    (tmp_path / "rpfm_cli.exe").write_text("")
    assert _preflight(_settings(helper=tmp_path)) == tmp_path


def test_preflight_fails_when_steam_drive_unset():
    with pytest.raises(PreflightError) as exc:
        _preflight(_settings(drive=""))
    assert "steam_library_drive" in exc.value.missing


# //////////////////////////////////////////////////////////////////////////////////////////////////
# //////////////////////////////////////////////////////////////////////////////////////////////////
# Lifecycle tests


@pytest.fixture(autouse=True)
def _reset_runner_state():
    """Clear runner state between tests so they cannot bleed into each other."""
    if sr._proc is not None and sr._proc.poll() is None:
        try:
            sr._proc.terminate()
            sr._proc.wait(timeout=5)
        except Exception:
            pass
    sr._current = None
    sr._proc = None
    sr._log.clear()
    yield


def test_start_run_executes_fixture_and_streams_lines():
    handle = start_run("_test_echo", _settings(), registry=_TEST_SCRIPT_REGISTRY)
    assert handle.script_id == "_test_echo"
    # Wait for completion (echo exits in <1s).
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        h = current_run()
        if h and h.exit_code is not None:
            break
        time.sleep(0.05)
    h = current_run()
    assert h is not None
    assert h.exit_code == 0
    # Buffer captured all 5 lines.
    lines = [line.line for line in list(_log)]
    assert any("line 0" in l for l in lines)
    assert any("line 4" in l for l in lines)


def test_start_run_runs_a_module_that_imports_a_sibling_package():
    """A module entry runs with `python -m` from the helper_scripts root, so its `from _test_core...` import resolves."""
    start_run("_test_module", _settings(), registry=_TEST_SCRIPT_REGISTRY)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        h = current_run()
        if h and h.exit_code is not None:
            break
        time.sleep(0.05)
    h = current_run()
    assert h is not None
    assert h.exit_code == 0
    assert any("hello from core" in line.line for line in list(_log))


def test_start_run_raises_when_already_running():
    start_run("_test_sleep", _settings(), registry=_TEST_SCRIPT_REGISTRY)
    try:
        with pytest.raises(RunInProgressError):
            start_run("_test_echo", _settings(), registry=_TEST_SCRIPT_REGISTRY)
    finally:
        cancel_run()


def test_cancel_run_terminates_long_running_subprocess():
    start_run("_test_sleep", _settings(), registry=_TEST_SCRIPT_REGISTRY)
    # Wait briefly to confirm it's running.
    time.sleep(0.5)
    assert current_run() is not None
    cancel_run()
    # Within 12s (terminate timeout 10 + 2 grace), the process should be gone.
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        h = current_run()
        if h and h.exit_code is not None:
            break
        time.sleep(0.1)
    h = current_run()
    assert h is not None
    assert h.exit_code is not None  # whatever code, just must be set


def test_cancel_run_idempotent_when_idle():
    cancel_run()  # does not raise


def test_unknown_script_id_raises():
    with pytest.raises(UnknownScriptError):
        start_run("does_not_exist", _settings(), registry=_TEST_SCRIPT_REGISTRY)
