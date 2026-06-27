# -*- coding: utf-8 -*-
"""
证书校验模块 —— 校园电视播放系统

在系统启动时校验上传的证书是否有效（签名 + 有效期）。
使用 RSA-4096 / SHA256-PSS 签名方案。
公钥在首次运行 cert_generator.py --gen-keys 后，通过 --show-public-key 获取并填入下方常量。
"""

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
except ImportError:
    hashes = None
    serialization = None
    padding = None
    default_backend = None


logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 公钥 —— 由 cert_generator.py --gen-keys 生成后，通过 --show-public-key 获取
# 将此 PEM 字符串完整替换为实际的公钥内容
# ═══════════════════════════════════════════════════════════════════════════
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAsLXDT8NZYlP4K6ylzqT4
p7OzDUqwgG+ktuiADs7wC5gjexXJVsvqP+J2Kq2V7bMxEPCOPyahRG9o+0ZtJuty
viKK0WFrxRhYGxYfG90CN2IqLmGwg/eFXQk2ipZPb7Kg1hq8LqY1tohAISM62mtU
5K1xuzFmB3BzgyUPEGKYwH3jOHu3vPCvlUErhrBU1WJPIM9tnVuo9KoNBWYsB+JS
PCwieZ/LAnTU2x0JJ9OsDRGlqBC9ExbqOZ4I0oyFDI3DJzJCIYGdUFa+3e0MYXIi
kjW+ixIlsd8YeOwtYquHsQAgs+KwbUmfNTSh3i3vuKXMSxY2KEMTzm4cHwxX8eQz
0RHrfFoxEFTib7cwcpV3iFWszJMZIzjKPHcKDwDIDCS2LukbjVD6F1VGYhS2k/em
obwR4lf08kTHRHSqbfVK2p/2SGleBEaGECHqY6UJiapgsglOeZ/nGth4ZhBaICPs
pnoAXtokXL+ZgAH5MhpAcD9dSVpwB+iPsuDPstDuvpZc6LuSCm4wKxswUDHuoyeC
1blS47uZwIrS5eh7CeSGrg7TYsQs3syC2kN4DeQNlQyU0V5d2Zu4Z1CE8encm8+x
j51jqteV+n6xtFXRcHiimLvWtmeZYk7XzRtCdkd3Iesl4JT5ah99H68cWLb9gG59
fmd8/BXw48Pz353O0YoCY90CAwEAAQ==
-----END PUBLIC KEY-----"""

# 缓存的公钥对象
_public_key_cache = None

# 证书校验结果缓存（避免每次请求都重新解析）
_cert_check_cache = None
_cert_check_cache_time = None
_CERT_CHECK_CACHE_TTL = timedelta(minutes=5)


def _load_public_key():
    """加载（或返回缓存的）公钥对象。"""
    global _public_key_cache
    if _public_key_cache is not None:
        return _public_key_cache

    if any(x is None for x in [serialization, hashes, padding, default_backend]):
        logger.warning("cryptography 库未安装，证书校验不可用。")
        return None

    pem = PUBLIC_KEY_PEM.strip()
    if not pem:
        logger.warning("PUBLIC_KEY_PEM 未配置，证书校验不可用。")
        return None

    try:
        _public_key_cache = serialization.load_pem_public_key(
            pem.encode("utf-8"),
            backend=default_backend(),
        )
        return _public_key_cache
    except Exception as exc:
        logger.error("公钥加载失败: %s", exc)
        return None


def verify_signature(public_key, data: bytes, signature_b64: str) -> bool:
    """用 RSA 公钥验证 PSS 签名。"""
    try:
        signature = base64.b64decode(signature_b64)
        public_key.verify(
            signature,
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def parse_certificate_json(raw_text: str) -> Optional[dict]:
    """解析证书 JSON 文本。"""
    if not raw_text or not raw_text.strip():
        return None
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        logger.warning("证书 JSON 解析失败。")
        return None


def validate_certificate(cert_json_text: str) -> dict:
    """
    校验证书。

    返回:
        {
            "valid": True/False,
            "customer": "客户名称",
            "expire_date": "2026-12-31",
            "remaining_days": 180,
            "error": "错误信息（仅在 valid=False 时）"
        }
    """
    result = {
        "valid": False,
        "customer": "",
        "issue_date": "",
        "expire_date": "",
        "remaining_days": 0,
        "error": "",
    }

    # 解析 JSON
    cert = parse_certificate_json(cert_json_text)
    if not cert:
        result["error"] = "证书格式无效：无法解析 JSON。"
        return result

    # 检查必要字段
    required = ["customer", "issue_date", "expire_date", "product", "version", "signature"]
    missing = [k for k in required if k not in cert]
    if missing:
        result["error"] = f"证书缺少必要字段: {', '.join(missing)}"
        return result

    # 检查产品
    if cert.get("product") != "youthPlayer":
        result["error"] = f"产品不匹配: {cert.get('product')}"
        return result

    # 检查版本
    if cert.get("version", 0) < 1:
        result["error"] = "证书版本过低，请更新证书。"
        return result

    # 提取签名字段
    signature = cert.pop("signature", "")
    sign_data_raw = json.dumps(
        {k: cert[k] for k in sorted(cert)},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # 验证签名
    public_key = _load_public_key()
    if public_key is None:
        result["error"] = "证书系统未初始化（公钥缺失）。"
        return result

    if not verify_signature(public_key, sign_data_raw.encode("utf-8"), signature):
        result["error"] = "签名验证失败！证书可能被篡改或已损坏。"
        return result

    # 检查日期
    try:
        expire_date = datetime.strptime(cert["expire_date"], "%Y-%m-%d")
        issue_date = datetime.strptime(cert["issue_date"], "%Y-%m-%d")
    except ValueError as e:
        result["error"] = f"日期格式错误: {e}"
        return result

    now = datetime.now()
    remaining_days = (expire_date - now).days

    result["customer"] = cert.get("customer", "")
    result["issue_date"] = cert["issue_date"]
    result["expire_date"] = cert["expire_date"]
    result["remaining_days"] = max(0, remaining_days)

    if now > expire_date:
        result["error"] = f"证书已过期（过期日期: {cert['expire_date']}，已过期 {abs(remaining_days)} 天）。"
        return result

    # 全部通过
    result["valid"] = True
    result["error"] = ""
    return result


def check_certificate_valid(cert_json_text: Optional[str]) -> dict:
    """
    检查证书是否有效（带缓存，按证书内容缓存）。
    与 validate_certificate 返回相同的结构。
    """
    global _cert_check_cache, _cert_check_cache_time

    now = datetime.now()
    cache_key = (cert_json_text or "").strip()

    if (
        _cert_check_cache is not None
        and _cert_check_cache_time is not None
        and (now - _cert_check_cache_time) < _CERT_CHECK_CACHE_TTL
    ):
        return _cert_check_cache

    if not cache_key:
        result = {
            "valid": False,
            "customer": "",
            "issue_date": "",
            "expire_date": "",
            "remaining_days": 0,
            "error": "未上传证书文件。请在系统设置中上传有效的 .lic 证书。",
        }
        _cert_check_cache = result
        _cert_check_cache_time = now
        return result

    result = validate_certificate(cert_json_text)
    _cert_check_cache = result
    _cert_check_cache_time = now
    return result


def invalidate_cache():
    """清除证书校验缓存（上传新证书后调用）。"""
    global _cert_check_cache, _cert_check_cache_time
    _cert_check_cache = None
    _cert_check_cache_time = None


def get_cert_expire_warning(cert_result: dict) -> Optional[str]:
    """如果证书即将过期（30 天内），返回警告文本；否则返回 None。"""
    if not cert_result.get("valid"):
        return None
    remaining = cert_result.get("remaining_days", 0)
    if remaining <= 30:
        return (
            f"⚠️ 证书将在 {remaining} 天后过期，请及时续期。"
            if remaining > 0
            else "⚠️ 证书今天过期！"
        )
    return None
