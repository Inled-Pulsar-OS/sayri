"""Desktop OTP pairing: only the paired owner may use the gateway.

A 6-digit PIN (0600) is compared in constant time, checked against
``expires_at`` and rotated (no replay); group/other-readable secrets are
refused, and failures persist so the 5/10min lockout sticks."""
import hmac, json, os, random, time
from pathlib import Path


def _private(path: Path) -> bool:
    try:
        return path.is_file() and (path.stat().st_mode & 0o077) == 0
    except OSError:
        return False


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(payload, indent=2).encode())
    finally:
        os.close(fd)


def _read_private(path: Path) -> dict:
    if not _private(path):
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class AuthorizationManager:
    MAX_FAILED = 5
    LOCKOUT = 600
    PIN_TTL = 86400

    def __init__(self, auth_file, pin_file, fail_file) -> None:
        self.auth_file, self.pin_file, self.fail_file = (
            Path(auth_file), Path(pin_file), Path(fail_file))
        self.owners: set = set()
        self.failures = {}
        for user, value in _read_private(self.fail_file).items():
            try:
                self.failures[str(user)] = [int(value[0]), float(value[1])]
            except (TypeError, ValueError, IndexError, KeyError):
                pass
        self._reload()

    def _reload(self) -> None:
        data = _read_private(self.auth_file)
        self.owners = {str(u).lower()
                       for u in data.get("allowed_discord_users", [])}

    def is_owner(self, user_id, username=None) -> bool:
        self._reload()
        name = (username or "").lower()
        return bool(str(user_id).lower() in self.owners
                    or name and (name in self.owners or "@" + name in self.owners))

    def pair(self, pin: str, user_id: str, username=None):
        """Pair ``user_id`` with the desktop PIN: returns (ok, message)."""
        now, user = time.time(), str(user_id)
        tries, last = self.failures.get(user, [0, 0.0])
        if now - last > self.LOCKOUT:
            tries = 0
        if tries >= self.MAX_FAILED:
            wait = max(1, int(self.LOCKOUT - (now - last)))
            return False, f"Too many failed attempts. Try again in {wait}s."
        supplied = pin.replace(" ", "").replace("-", "").strip()
        expected = _read_private(self.pin_file)
        stored = str(expected.get("pin", "")).replace(" ", "")
        expires = float(expected.get("expires_at", 0) or 0)
        if not (supplied.isdigit() and len(supplied) == 6):
            return self._reject(user, tries, now, "PIN must be 6 digits.")
        if not (stored and hmac.compare_digest(supplied, stored)
                and now <= expires):
            return self._reject(user, tries, now, "Incorrect or expired PIN.")
        self.owners.update(filter(None, (user.lower(), (username or "").lower())))
        _write_private(self.auth_file, {"version": 1, "updated_at": now,
                       "allowed_discord_users": sorted(self.owners)})
        self.failures.pop(user, None)
        _write_private(self.fail_file, self.failures)
        _write_private(self.pin_file, {"pin": str(random.randint(100000, 999999)),
                       "created_at": now, "expires_at": now + self.PIN_TTL})
        return True, "Pairing complete: this Discord account is now the owner."

    def _reject(self, user, tries, now, reason):
        self.failures[user] = [tries + 1, now]
        _write_private(self.fail_file, self.failures)
        left = max(0, self.MAX_FAILED - tries - 1)
        return False, f"{reason} Attempts left: {left}."