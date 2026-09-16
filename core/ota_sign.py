"""OTA 簽章（owner 2026-09-16「這樣風險偏高」）：主控用**只有它有**的私鑰簽 update_agent.py，機器用公鑰驗，驗不過就不跑。

- 私鑰 `ota_signing_key.pem`：只放主控的安裝目錄（C:\\OriginsunAgent），**不進 git、不進 OTA ZIP**（不在 AGENT_FILES；.gitignore 有它）。
  dev 機要簽給 8001 測試用的話，同一把複製到 E:\\Dev（也 gitignore）。
- 公鑰 `ota_signing_pub.pem`：進 git、在 AGENT_FILES，隨 OTA 到每一台機器。
- 演算法 Ed25519（cryptography 隨 google-auth 在機隊清單裡）。簽的是**檔案原文的 bytes**，簽章以 hex 放在回應的
  `X-Originsun-Signature` header；機器端 core/process_spawn._fresh_updater 驗過才跑，沒簽章／驗不過／沒公鑰／沒 cryptography
  一律當沒拿到（退回本機那支）—— **fail closed**。
- 換鑰：先用舊私鑰簽一版帶新公鑰的 OTA 推出去，再換主控的私鑰。私鑰外洩＝攻擊者能簽，換鑰是唯一補救。

這裡是純函式（讀檔、簽、驗），不碰網路、不碰 FastAPI。
"""
from __future__ import annotations

import os
from typing import Optional

PRIVATE_KEY_FILE = "ota_signing_key.pem"
PUBLIC_KEY_FILE = "ota_signing_pub.pem"
SIGNATURE_HEADER = "X-Originsun-Signature"


def generate_keypair(priv_path: str, pub_path: str) -> None:
    """產一對 Ed25519 金鑰（PEM）。只在建立／換鑰時手動跑一次。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    priv = ed25519.Ed25519PrivateKey.generate()
    with open(priv_path, "wb") as f:
        f.write(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    with open(pub_path, "wb") as f:
        f.write(priv.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo))


def sign(data: bytes, priv_path: str) -> Optional[str]:
    """用私鑰簽 data → hex；私鑰檔不在／讀不了回 None（主控就回沒簽章的回應，機器會拒收）。"""
    try:
        from cryptography.hazmat.primitives import serialization
        with open(priv_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        return key.sign(data).hex()
    except Exception:
        return None


def verify(data: bytes, sig_hex: Optional[str], pub_path: str) -> bool:
    """公鑰驗簽。任何一環不對（沒簽章、格式壞、公鑰不在、簽章不符、沒 cryptography）都回 False —— 不丟例外，呼叫端一律 fail closed。"""
    if not sig_hex or not os.path.isfile(pub_path):
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        with open(pub_path, "rb") as f:
            pub = serialization.load_pem_public_key(f.read())
        pub.verify(bytes.fromhex(sig_hex.strip()), data)
        return True
    except Exception:
        return False
