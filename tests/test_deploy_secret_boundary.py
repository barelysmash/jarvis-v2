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


def test_deploy_assets_are_pinned_to_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "*.sh text eol=lf" in attributes
    assert "deploy/deploy.config text eol=lf" in attributes
    assert "*.service text eol=lf" in attributes
    assert "*.timer text eol=lf" in attributes


def test_bastion_stage_normalizes_public_deploy_assets() -> None:
    deploy_script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")

    assert "find ${BASTION_STAGING} -type f" in deploy_script
    assert "-name '*.sh'" in deploy_script
    assert "-name 'deploy.config'" in deploy_script
    assert "-name '*.service'" in deploy_script
    assert "-name '*.timer'" in deploy_script
    assert "-exec sed -i 's/\\r$//' {} +" in deploy_script


def test_deploy_client_builds_and_packages_the_production_hud() -> None:
    deploy_script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")

    assert "build_hud() {" in deploy_script
    assert "npm ci --include=dev --prefer-offline --no-audit --no-fund" in deploy_script
    assert "npm run build" in deploy_script
    assert "--exclude='hud/dist'" not in deploy_script
    assert "grep '/hud/dist/index.html$'" in deploy_script

    hud_build = deploy_script.index("            build_hud")
    release_build = deploy_script.index("            build_release")
    assert hud_build < release_build


def test_remote_install_rejects_missing_hud_before_symlink_swap() -> None:
    installer = (DEPLOY / "remote_install.sh").read_text(encoding="utf-8")

    hud_guard = installer.index('[[ ! -s "${RELEASE_PATH}/hud/dist/index.html" ]]')
    symlink_swap = installer.index('mv "${CURRENT_LINK}" "${PREVIOUS_LINK}"')

    assert hud_guard < symlink_swap
    assert "release is missing the built JARVIS HUD" in installer


def test_ci_builds_and_verifies_the_production_hud() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "uses: actions/setup-node@v4" in workflow
    assert "cache-dependency-path: hud/package-lock.json" in workflow
    assert "run: npm ci --include=dev --no-audit --no-fund" in workflow
    assert "run: npm run build" in workflow
    assert "run: test -s dist/index.html" in workflow


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
