"""LDAP authentication helper."""

import logging
from dataclasses import dataclass, field
from typing import List

try:  # pragma: no cover
    from ldap3 import BASE, Connection, Server, SUBTREE
    from ldap3.utils.conv import escape_filter_chars
except Exception:  # pragma: no cover
    BASE = None
    Connection = None
    Server = None
    SUBTREE = None

    def escape_filter_chars(value):
        return str(value or "")


logger = logging.getLogger(__name__)


@dataclass
class LDAPAuthResult:
    ok: bool
    username: str
    user_dn: str = ""
    groups: List[str] = field(default_factory=list)
    error: str = ""


def _split_csv(text):
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


def _normalize_dn(value):
    return str(value or "").strip().lower()


def group_intersects(user_groups, configured_groups):
    if isinstance(configured_groups, str):
        configured = _split_csv(configured_groups)
    else:
        configured = [str(item).strip() for item in (configured_groups or []) if str(item).strip()]
    if not configured:
        return False
    target = {_normalize_dn(item) for item in configured}
    current = {_normalize_dn(item) for item in (user_groups or []) if str(item).strip()}
    return bool(target & current)


def _bind_server(server, user, password, timeout):
    conn = Connection(
        server,
        user=user,
        password=password,
        auto_bind=True,
        receive_timeout=timeout,
    )
    conn.unbind()
    return True


def _extract_groups(entry, group_attr):
    if not group_attr:
        return []
    if group_attr not in entry:
        return []
    try:
        return [str(item).strip() for item in entry[group_attr].values if str(item).strip()]
    except Exception:
        return []


def _load_groups_by_user_dn(server, user_dn, password, group_attr, timeout):
    if not user_dn or not group_attr or BASE is None:
        return []
    conn = None
    try:
        conn = Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
            receive_timeout=timeout,
        )
        ok = conn.search(
            search_base=user_dn,
            search_filter="(objectClass=*)",
            search_scope=BASE,
            attributes=[group_attr],
            size_limit=1,
        )
        if not ok or len(conn.entries) != 1:
            return []
        return _extract_groups(conn.entries[0], group_attr)
    except Exception:
        return []
    finally:
        try:
            if conn:
                conn.unbind()
        except Exception:
            pass


def authenticate(config, username, password):
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        return LDAPAuthResult(False, username, error="empty_username_or_password")

    if Connection is None or Server is None:
        return LDAPAuthResult(False, username, error="ldap3_not_installed")

    server_uri = str(getattr(config, "LDAP_SERVER_URI", "") or "").strip()
    if not server_uri:
        return LDAPAuthResult(False, username, error="ldap_server_missing")

    timeout = float(getattr(config, "LDAP_CONNECT_TIMEOUT", 5.0) or 5.0)
    use_ssl = bool(getattr(config, "LDAP_USE_SSL", False))

    try:
        server = Server(server_uri, use_ssl=use_ssl, connect_timeout=timeout, get_info=None)
    except Exception as exc:  # pragma: no cover
        logger.warning("LDAP server init failed: %s", exc)
        return LDAPAuthResult(False, username, error=f"ldap_server_init_failed: {exc}")

    user_dn_template = str(getattr(config, "LDAP_USER_DN_TEMPLATE", "") or "").strip()
    base_dn = str(getattr(config, "LDAP_BASE_DN", "") or "").strip()
    group_attr = str(getattr(config, "LDAP_GROUP_ATTR", "memberOf") or "memberOf").strip()
    user_filter = str(getattr(config, "LDAP_USER_FILTER", "(sAMAccountName={username})") or "").strip()
    if not user_filter:
        user_filter = "(sAMAccountName={username})"

    groups = []
    user_dn = ""

    if user_dn_template:
        user_dn = user_dn_template.replace("{username}", username).strip()
        if not user_dn:
            return LDAPAuthResult(False, username, error="ldap_user_dn_template_invalid")

    search_conn = None
    try:
        if not user_dn:
            if not base_dn:
                return LDAPAuthResult(False, username, error="ldap_base_dn_missing")

            bind_dn = str(getattr(config, "LDAP_BIND_DN", "") or "").strip()
            bind_password = str(getattr(config, "LDAP_BIND_PASSWORD", "") or "")

            if bind_dn:
                search_conn = Connection(
                    server,
                    user=bind_dn,
                    password=bind_password,
                    auto_bind=True,
                    receive_timeout=timeout,
                )
            else:
                search_conn = Connection(server, auto_bind=True, receive_timeout=timeout)

            safe_username = escape_filter_chars(username)
            search_filter = user_filter.replace("{username}", safe_username)
            attrs = [group_attr] if group_attr else []
            ok = search_conn.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=attrs,
                size_limit=2,
            )
            if not ok or len(search_conn.entries) != 1:
                return LDAPAuthResult(False, username, error="ldap_user_not_unique_or_not_found")

            entry = search_conn.entries[0]
            user_dn = str(entry.entry_dn or "").strip()
            if not user_dn:
                return LDAPAuthResult(False, username, error="ldap_user_dn_empty")

            groups = _extract_groups(entry, group_attr)

        _bind_server(server, user_dn, password, timeout)
    except Exception as exc:
        logger.info("LDAP auth failed for %s: %s", username, exc)
        return LDAPAuthResult(False, username, error=f"ldap_bind_failed: {exc}")
    finally:
        try:
            if search_conn:
                search_conn.unbind()
        except Exception:
            pass

    if group_attr and not groups and user_dn:
        groups = _load_groups_by_user_dn(server, user_dn, password, group_attr, timeout)

    allowed_groups = str(getattr(config, "LDAP_ALLOWED_GROUPS", "") or "").strip()
    if allowed_groups and not group_intersects(groups, allowed_groups):
        return LDAPAuthResult(False, username, error="ldap_group_not_allowed")

    return LDAPAuthResult(True, username=username, user_dn=user_dn, groups=groups, error="")
