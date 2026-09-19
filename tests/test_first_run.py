# -*- coding: utf-8 -*-
"""首次启动引导与密码重置 CLI（2026-09-19 可用性 P0-U1/U2）。

覆盖：
- `_ensure_env_file`：缺失生成 / 幂等 / 显式密钥跳过（期望值来自 U1 规格，
  非被测代码自洽——判据是「跑完 run_app 后 create_app 不再因 secret_key 拒启」）。
- `Settings.frontend`：.env/环境变量收编后可经 Settings 生效（P2 修复回归）。
- SPA 模式 `GET /logout` → 405（与 legacy 口径对齐）。
- `cli.reset_password`：建/改密码 + 校验哈希 + 参数防线。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from campus_ids.runtime.db import get_connection, init_db
from campus_ids.runtime.repositories import UserRepository
from campus_ids.runtime.settings import get_settings, reset_settings
from campus_ids.web_new.app import _ensure_env_file
from campus_ids.web_new.auth import verify_password
from campus_ids.web_new.cli import reset_password


# ── _ensure_env_file ─────────────────────────────────────────────

def test_ensure_env_file_generates_with_random_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMPUS_IDS_SECRET_KEY", raising=False)
    env_path = tmp_path / ".env"

    assert _ensure_env_file(env_path) is True
    content = env_path.read_text(encoding="utf-8")
    assert "CAMPUS_IDS_SECRET_KEY=" in content

    secret = content.split("CAMPUS_IDS_SECRET_KEY=", 1)[1].splitlines()[0].strip()
    assert secret and secret != "change-me-in-production"
    assert len(secret) >= 32  # token_hex(32) = 64 hex chars


def test_ensure_env_file_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMPUS_IDS_SECRET_KEY", raising=False)
    env_path = tmp_path / ".env"
    assert _ensure_env_file(env_path) is True
    first = env_path.read_text(encoding="utf-8")

    assert _ensure_env_file(env_path) is False  # 已存在不覆盖
    assert env_path.read_text(encoding="utf-8") == first


def test_ensure_env_file_skips_when_secret_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPUS_IDS_SECRET_KEY", "user-provided-key")
    env_path = tmp_path / ".env"
    assert _ensure_env_file(env_path) is False
    assert not env_path.exists()


# ── Settings.frontend（P2：.env 静默忽略修复）─────────────────────

def test_frontend_field_default_and_env_override(monkeypatch):
    reset_settings()
    try:
        assert get_settings().frontend == "legacy"
    finally:
        reset_settings()

    monkeypatch.setenv("CAMPUS_IDS_FRONTEND", "new")
    reset_settings()
    try:
        assert get_settings().frontend == "new"
    finally:
        reset_settings()


def test_spa_mode_serves_dist_and_logout_is_405(monkeypatch):
    """SPA 模式：/ 是 dist 产物；GET /logout 不再被 fallback 吃掉返回 200。"""
    monkeypatch.setenv("CAMPUS_IDS_FRONTEND", "new")
    reset_settings()
    try:
        from campus_ids.web_new.app import create_app

        app = create_app()
        with TestClient(app) as client:
            r_index = client.get("/")
            assert r_index.status_code == 200
            assert 'id="app"' in r_index.text  # dist 标记，非 legacy Jinja

            r_logout = client.get("/logout")
            assert r_logout.status_code == 405  # 与 legacy 口径对齐
    finally:
        reset_settings()


# ── reset-password CLI ───────────────────────────────────────────

@pytest.fixture(autouse=True)
def _schema():
    init_db()


def _user_hash(username: str) -> str:
    with get_connection() as conn:
        row = UserRepository.get_by_username(conn, username)
        return row.password_hash if row else ""


def test_reset_password_creates_missing_user():
    assert UserRepository.get_by_username.__name__  # sanity：仓库可导入
    assert _user_hash("recovery-tester") == ""

    rc = reset_password(["--user", "recovery-tester", "--password", "s3cret-pw"])
    assert rc == 0

    h = _user_hash("recovery-tester")
    assert h.startswith("pbkdf2:")  # 哈希落库而非明文
    assert verify_password("s3cret-pw", h)


def test_reset_password_updates_existing_user():
    reset_password(["--user", "recovery-tester", "--password", "old-password"])
    old_hash = _user_hash("recovery-tester")

    rc = reset_password(["--user", "recovery-tester", "--password", "new-password"])
    assert rc == 0

    new_hash = _user_hash("recovery-tester")
    assert new_hash != old_hash
    assert verify_password("new-password", new_hash)
    assert not verify_password("old-password", new_hash)


def test_reset_password_random_generation(capsys):
    rc = reset_password(["--user", "recovery-tester"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "新密码:" in out
    pwd = out.split("新密码:", 1)[1].splitlines()[0].strip()
    assert len(pwd) >= 12
    assert verify_password(pwd, _user_hash("recovery-tester"))


def test_reset_password_rejects_bad_params(capsys):
    reset_password(["--user", "recovery-tester", "--password", "keep-me-pw"])
    before = _user_hash("recovery-tester")
    assert before  # 造一个用户，验证失败调用不破坏既有数据

    assert reset_password(["--user", " ", "--password", "whatever"]) == 2
    assert reset_password(["--user", "x", "--password", "abc"]) == 2  # <6 位
    assert _user_hash("recovery-tester") == before
