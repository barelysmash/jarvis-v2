from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
PERSISTENT_ENV = "%h/jarvis-data/env/jarvis.env"
LEGACY_ENV = "%h/jarvis/deploy/.env"


def test_deploy_client_does_not_require_or_ship_private_env() -> None:
    deploy_script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    handoff_script = (DEPLOY / "handoff.sh").read_text(encoding="utf-8")

    assert '[[ ! -f "${SCRIPT_DIR}/.env" ]]' not in deploy_script
    assert '"${SCRIPT_DIR}/.env"' not in deploy_script
    assert '"${SCRIPT_DIR}/.env"' not in handoff_script
    assert '"${GUILD_STAGING}/.env"' not in handoff_script


def test_target_config_declares_persistent_private_env() -> None:
    config = (DEPLOY / "deploy.config").read_text(encoding="utf-8")

    assert 'TARGET_ENV_DIR="${TARGET_DATA}/env"' in config
    assert 'TARGET_ENV="${TARGET_ENV_DIR}/jarvis.env"' in config


def test_preflight_checks_remote_env_without_nested_shell() -> None:
    deploy_script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")

    assert "sudo -u ${TARGET_USER} -H test -f '${TARGET_ENV}'" in deploy_script
    assert (
        "sudo -u ${TARGET_USER} -H test -f "
        "'${TARGET_INSTALL}/deploy/.env'"
    ) in deploy_script
    assert "sudo -u ${TARGET_USER} -H sh -c 'if [ -f" not in deploy_script


def test_remote_install_adopts_legacy_env_before_symlink_swap() -> None:
    installer = (DEPLOY / "remote_install.sh").read_text(encoding="utf-8")

    adoption = installer.index('install -m 600 "${LEGACY_ENV}" "${TARGET_ENV}"')
    symlink_swap = installer.index('mv "${CURRENT_LINK}" "${PREVIOUS_LINK}"')

    assert adoption < symlink_swap
    assert 'chmod 700 "${TARGET_ENV_DIR}"' in installer
    assert 'chmod 600 "${TARGET_ENV}"' in installer


def test_all_systemd_services_use_persistent_private_env() -> None:
    services = sorted((DEPLOY / "systemd").glob("*.service"))

    assert services
    for service in services:
        text = service.read_text(encoding="utf-8")
        assert f"EnvironmentFile={PERSISTENT_ENV}" in text
        assert LEGACY_ENV not in text
