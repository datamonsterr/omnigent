"""Conversation label keys that carry a session's account identity.

The runner reads the account's credential home from the session labels, so
these keys are the wire contract between the server and the runner. They are
namespaced under ``omnigent.`` like every other session label.
"""

from __future__ import annotations

#: The account the session runs under, e.g. ``"codex-acc1"``.
ACCOUNT_ID_LABEL_KEY = "omnigent.account.id"

#: The pool the account belongs to, e.g. ``"codex-native"``.
ACCOUNT_POOL_LABEL_KEY = "omnigent.account.pool"

#: The Codex credential home the session runs under.
CODEX_HOME_LABEL_KEY = "omnigent.codex.codex_home"

#: The Antigravity credential home the session runs under.
GEMINI_DIR_LABEL_KEY = "omnigent.antigravity.gemini_dir"

#: Legacy label key accepted as an account pin.
LEGACY_ACCOUNT_ID_LABEL_KEY = "account_id"

#: Label keys that pin an account to a credential home.
ACCOUNT_HOME_LABEL_KEYS = (CODEX_HOME_LABEL_KEY, GEMINI_DIR_LABEL_KEY)

__all__ = [
    "ACCOUNT_HOME_LABEL_KEYS",
    "ACCOUNT_ID_LABEL_KEY",
    "ACCOUNT_POOL_LABEL_KEY",
    "CODEX_HOME_LABEL_KEY",
    "GEMINI_DIR_LABEL_KEY",
    "LEGACY_ACCOUNT_ID_LABEL_KEY",
]
