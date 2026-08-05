class SgError(Exception):
    """Base error for skill-groups. code maps to CLI exit code."""
    code = 1


class UserError(SgError):
    """User input error (bad group name, conflicts, etc.) -> exit 1."""
    code = 1


class EnvError(SgError):
    """Environment/IO error (missing git, corrupt config, IO failure) -> exit 2."""
    code = 2
