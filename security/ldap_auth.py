"""LDAP authentication helper."""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

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


@dataclass
class LDAPProbeResult:
    ok: bool
    message: str = ""
    error: str = ""
    detected_base_dn: str = ""
    naming_contexts: List[str] = field(default_factory=list)
    effective_use_ssl: bool = False
    bind_mode: str = "anonymous"


@dataclass
class LDAPDirectoryUser:
    username: str
    user_dn: str = ""
    groups: List[str] = field(default_factory=list)


@dataclass
class LDAPDirectorySyncResult:
    ok: bool
    users: List[LDAPDirectoryUser] = field(default_factory=list)
    scanned_entries: int = 0
    selected_entries: int = 0
    error: str = ""
    message: str = ""


def _cfg_get(config, key, default=None):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


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


def _extract_entry_values(entry, attr_name):
    if not attr_name:
        return []
    if attr_name not in entry:
        return []
    try:
        values = entry[attr_name].values
    except Exception:
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _detect_ssl_from_uri(server_uri) -> Optional[bool]:
    text = str(server_uri or "").strip().lower()
    if text.startswith("ldaps://"):
        return True
    if text.startswith("ldap://"):
        return False
    return None


def _resolve_ssl(server_uri, configured_ssl):
    detected = _detect_ssl_from_uri(server_uri)
    if detected is None:
        return bool(configured_ssl)
    return bool(detected)


def _server_from_config(config):
    if Connection is None or Server is None:
        return None, "ldap3_not_installed", False, ""

    server_uri = str(_cfg_get(config, "LDAP_SERVER_URI", "") or "").strip()
    if not server_uri:
        return None, "ldap_server_missing", False, ""

    timeout = float(_cfg_get(config, "LDAP_CONNECT_TIMEOUT", 5.0) or 5.0)
    use_ssl = _resolve_ssl(server_uri, _cfg_get(config, "LDAP_USE_SSL", False))
    try:
        server = Server(server_uri, use_ssl=use_ssl, connect_timeout=timeout, get_info=None)
    except Exception as exc:
        logger.warning("LDAP server init failed: %s", exc)
        return None, f"ldap_server_init_failed: {exc}", use_ssl, server_uri
    return server, "", use_ssl, server_uri


def _username_attr_from_filter(user_filter):
    text = str(user_filter or "")
    match = re.search(r"([A-Za-z][A-Za-z0-9._-]*)\s*=\s*\{username\}", text)
    if not match:
        return ""
    return match.group(1).strip()


def _first_nonempty_attr(entry, attr_names):
    for attr in attr_names:
        values = _extract_entry_values(entry, attr)
        if values:
            return values[0].strip()
    return ""


def _read_root_dse(conn):
    if BASE is None:
        return "", []
    ok = conn.search(
        search_base="",
        search_filter="(objectClass=*)",
        search_scope=BASE,
        attributes=["defaultNamingContext", "namingContexts"],
        size_limit=1,
    )
    if not ok or len(conn.entries) < 1:
        return "", []
    entry = conn.entries[0]
    default_base = ""
    default_values = _extract_entry_values(entry, "defaultNamingContext")
    if default_values:
        default_base = default_values[0]
    naming_contexts = _extract_entry_values(entry, "namingContexts")
    if not default_base and naming_contexts:
        default_base = naming_contexts[0]
    return default_base, naming_contexts


def probe_connection(config):
    server, error, effective_use_ssl, _server_uri = _server_from_config(config)
    if error:
        return LDAPProbeResult(ok=False, error=error)

    timeout = float(_cfg_get(config, "LDAP_CONNECT_TIMEOUT", 5.0) or 5.0)
    bind_dn = str(_cfg_get(config, "LDAP_BIND_DN", "") or "").strip()
    bind_password = str(_cfg_get(config, "LDAP_BIND_PASSWORD", "") or "")
    base_dn = str(_cfg_get(config, "LDAP_BASE_DN", "") or "").strip()
    bind_mode = "credential_bind" if bind_dn else "anonymous_bind"
    conn = None
    try:
        if bind_dn:
            conn = Connection(
                server,
                user=bind_dn,
                password=bind_password,
                auto_bind=True,
                receive_timeout=timeout,
            )
        else:
            conn = Connection(server, auto_bind=True, receive_timeout=timeout)

        detected_base_dn, naming_contexts = _read_root_dse(conn)

        if base_dn and BASE is not None:
            ok = conn.search(
                search_base=base_dn,
                search_filter="(objectClass=*)",
                search_scope=BASE,
                attributes=["distinguishedName"],
                size_limit=1,
            )
            if not ok or len(conn.entries) < 1:
                return LDAPProbeResult(
                    ok=False,
                    error="ldap_base_dn_not_found",
                    detected_base_dn=detected_base_dn,
                    naming_contexts=naming_contexts,
                    effective_use_ssl=effective_use_ssl,
                    bind_mode=bind_mode,
                )

        message = "LDAP connection is healthy."
        if detected_base_dn:
            message += f" detected_base_dn={detected_base_dn}"
        return LDAPProbeResult(
            ok=True,
            message=message,
            detected_base_dn=detected_base_dn,
            naming_contexts=naming_contexts,
            effective_use_ssl=effective_use_ssl,
            bind_mode=bind_mode,
        )
    except Exception as exc:
        logger.info("LDAP probe failed: %s", exc)
        return LDAPProbeResult(
            ok=False,
            error=f"ldap_probe_failed: {exc}",
            effective_use_ssl=effective_use_ssl,
            bind_mode=bind_mode,
        )
    finally:
        try:
            if conn:
                conn.unbind()
        except Exception:
            pass


def sync_directory_users(config, max_entries=500):
    server, error, _effective_use_ssl, _server_uri = _server_from_config(config)
    if error:
        return LDAPDirectorySyncResult(ok=False, error=error)

    timeout = float(_cfg_get(config, "LDAP_CONNECT_TIMEOUT", 5.0) or 5.0)
    base_dn = str(_cfg_get(config, "LDAP_BASE_DN", "") or "").strip()
    if not base_dn:
        return LDAPDirectorySyncResult(ok=False, error="ldap_base_dn_missing")

    bind_dn = str(_cfg_get(config, "LDAP_BIND_DN", "") or "").strip()
    bind_password = str(_cfg_get(config, "LDAP_BIND_PASSWORD", "") or "")
    group_attr = str(_cfg_get(config, "LDAP_GROUP_ATTR", "memberOf") or "memberOf").strip()
    user_filter = str(_cfg_get(config, "LDAP_USER_FILTER", "(sAMAccountName={username})") or "").strip()
    if not user_filter:
        user_filter = "(sAMAccountName={username})"
    search_filter = user_filter.replace("{username}", "*")

    username_attr = _username_attr_from_filter(user_filter)
    username_attrs = []
    for item in [username_attr, "sAMAccountName", "uid", "cn", "mail", "userPrincipalName"]:
        item = str(item or "").strip()
        if item and item not in username_attrs:
            username_attrs.append(item)
    attrs = list(username_attrs)
    if group_attr and group_attr not in attrs:
        attrs.append(group_attr)

    limit = int(max_entries or 500)
    limit = max(1, min(5000, limit))

    conn = None
    try:
        if bind_dn:
            conn = Connection(
                server,
                user=bind_dn,
                password=bind_password,
                auto_bind=True,
                receive_timeout=timeout,
            )
        else:
            conn = Connection(server, auto_bind=True, receive_timeout=timeout)

        ok = conn.search(
            search_base=base_dn,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=attrs,
            size_limit=limit,
        )
        if not ok:
            return LDAPDirectorySyncResult(ok=False, error="ldap_directory_search_failed")

        allowed_groups = str(_cfg_get(config, "LDAP_ALLOWED_GROUPS", "") or "").strip()
        users = []
        seen = set()
        scanned = len(conn.entries)
        for entry in conn.entries:
            username = _first_nonempty_attr(entry, username_attrs)
            user_dn = str(getattr(entry, "entry_dn", "") or "").strip()
            if not username or not user_dn:
                continue
            username_key = username.lower()
            if username_key in seen:
                continue
            groups = _extract_groups(entry, group_attr) if group_attr else []
            if allowed_groups and not group_intersects(groups, allowed_groups):
                continue
            seen.add(username_key)
            users.append(LDAPDirectoryUser(username=username, user_dn=user_dn, groups=groups))

        return LDAPDirectorySyncResult(
            ok=True,
            users=users,
            scanned_entries=scanned,
            selected_entries=len(users),
            message=f"ldap_directory_sync_ready scanned={scanned} selected={len(users)}",
        )
    except Exception as exc:
        logger.info("LDAP directory sync failed: %s", exc)
        return LDAPDirectorySyncResult(ok=False, error=f"ldap_directory_sync_failed: {exc}")
    finally:
        try:
            if conn:
                conn.unbind()
        except Exception:
            pass


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

    server, error, _effective_use_ssl, _server_uri = _server_from_config(config)
    if error:
        return LDAPAuthResult(False, username, error=error)
    timeout = float(getattr(config, "LDAP_CONNECT_TIMEOUT", 5.0) or 5.0)

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
