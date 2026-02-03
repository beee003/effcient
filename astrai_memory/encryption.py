"""
Encryption utilities for Astrai Memory

Provides AES-256-GCM encryption for memory content.
Key derivation uses PBKDF2 with 600,000 iterations.

Usage:
    from astrai_memory.encryption import MemoryEncryption

    enc = MemoryEncryption(password="user_password")
    encrypted = enc.encrypt("secret memory")
    decrypted = enc.decrypt(encrypted)
"""

import base64
import hashlib
import os
import secrets
from typing import Optional

# Lazy import cryptography to handle broken installations
HAS_CRYPTOGRAPHY = None  # Will be set on first use
AESGCM = None
PBKDF2HMAC = None
hashes = None

def _check_cryptography():
    """Check if cryptography library is available (lazy load)"""
    global HAS_CRYPTOGRAPHY, AESGCM, PBKDF2HMAC, hashes
    if HAS_CRYPTOGRAPHY is not None:
        return HAS_CRYPTOGRAPHY
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes as _hashes
        AESGCM = _AESGCM
        PBKDF2HMAC = _PBKDF2HMAC
        hashes = _hashes
        HAS_CRYPTOGRAPHY = True
    except BaseException:
        # Catch ALL exceptions including pyo3 panics, rust errors, etc.
        HAS_CRYPTOGRAPHY = False
    return HAS_CRYPTOGRAPHY


class MemoryEncryption:
    """
    AES-256-GCM encryption for memory content.

    - Password-based key derivation (PBKDF2, 600k iterations)
    - Each encryption uses unique nonce (12 bytes)
    - Authenticated encryption (tampering detected)
    """

    SALT_SIZE = 16
    NONCE_SIZE = 12
    KEY_SIZE = 32  # AES-256
    ITERATIONS = 600_000  # OWASP recommendation

    def __init__(self, password: str, salt: Optional[bytes] = None):
        """
        Initialize encryption with password.

        Args:
            password: User's password (never stored)
            salt: Optional salt (generated if not provided)
        """
        if not _check_cryptography():
            raise ImportError(
                "cryptography library required for encryption. "
                "Install with: pip install cryptography"
            )

        self.salt = salt or secrets.token_bytes(self.SALT_SIZE)
        self.key = self._derive_key(password, self.salt)
        self.cipher = AESGCM(self.key)

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """Derive encryption key from password using PBKDF2"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self.KEY_SIZE,
            salt=salt,
            iterations=self.ITERATIONS,
        )
        return kdf.derive(password.encode())

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext string.

        Returns:
            Base64-encoded string: salt + nonce + ciphertext
        """
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        ciphertext = self.cipher.encrypt(nonce, plaintext.encode(), None)

        # Combine: salt (16) + nonce (12) + ciphertext
        combined = self.salt + nonce + ciphertext
        return base64.b64encode(combined).decode()

    def decrypt(self, encrypted: str) -> str:
        """
        Decrypt encrypted string.

        Args:
            encrypted: Base64-encoded encrypted data

        Returns:
            Original plaintext

        Raises:
            ValueError: If decryption fails (wrong password or tampered data)
        """
        try:
            combined = base64.b64decode(encrypted.encode())

            # Extract components
            salt = combined[:self.SALT_SIZE]
            nonce = combined[self.SALT_SIZE:self.SALT_SIZE + self.NONCE_SIZE]
            ciphertext = combined[self.SALT_SIZE + self.NONCE_SIZE:]

            # Re-derive key if salt differs (for decrypting data from different session)
            if salt != self.salt:
                raise ValueError("Salt mismatch - data encrypted with different key")

            plaintext = self.cipher.decrypt(nonce, ciphertext, None)
            return plaintext.decode()

        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")

    def get_salt_b64(self) -> str:
        """Get salt as base64 string (for storage)"""
        return base64.b64encode(self.salt).decode()

    @classmethod
    def from_salt_b64(cls, password: str, salt_b64: str) -> "MemoryEncryption":
        """Create encryption instance from stored salt"""
        salt = base64.b64decode(salt_b64.encode())
        return cls(password, salt)


class SimpleEncryption:
    """
    Fallback encryption when cryptography library not available.
    Uses XOR with SHA-256 derived key. NOT as secure as AES-GCM.
    """

    def __init__(self, password: str, salt: Optional[bytes] = None):
        self.salt = salt or os.urandom(16)
        # Derive key using PBKDF2-like approach with hashlib
        self.key = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode(),
            self.salt,
            100_000,  # iterations
            dklen=32
        )

    def encrypt(self, plaintext: str) -> str:
        """Simple XOR encryption (NOT recommended for production)"""
        data = plaintext.encode()
        # Extend key to match data length
        key_stream = (self.key * (len(data) // 32 + 1))[:len(data)]
        encrypted = bytes(a ^ b for a, b in zip(data, key_stream))
        combined = self.salt + encrypted
        return base64.b64encode(combined).decode()

    def decrypt(self, encrypted: str) -> str:
        """Simple XOR decryption"""
        combined = base64.b64decode(encrypted.encode())
        salt = combined[:16]
        data = combined[16:]

        if salt != self.salt:
            raise ValueError("Salt mismatch")

        key_stream = (self.key * (len(data) // 32 + 1))[:len(data)]
        decrypted = bytes(a ^ b for a, b in zip(data, key_stream))
        return decrypted.decode()

    def get_salt_b64(self) -> str:
        return base64.b64encode(self.salt).decode()


def get_encryption(password: str, salt: Optional[bytes] = None):
    """
    Get appropriate encryption implementation.
    Prefers AES-GCM, falls back to simple XOR.
    """
    if _check_cryptography():
        return MemoryEncryption(password, salt)
    else:
        print("Warning: Using simple encryption. Install 'cryptography' for AES-256-GCM.")
        return SimpleEncryption(password, salt)


def password_hash(password: str) -> str:
    """
    Hash password for verification (not for encryption).
    Used to check if user entered correct password.
    """
    salt = b"astrai_memory_v1"  # Fixed salt for verification hash
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100_000).hex()[:32]
