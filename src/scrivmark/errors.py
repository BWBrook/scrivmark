class ScrivError(Exception):
    """A refusal or failure to report to the user, without a traceback."""


class Conflict(ScrivError):
    """scrivmark cannot be sure where an operation applies, so it changed nothing."""


class StaleRevision(ScrivError):
    """The caller acted on state that has since changed."""
