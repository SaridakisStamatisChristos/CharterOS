class ApplicationError(Exception):
    """Base application-layer error."""


class EntityNotFoundError(ApplicationError):
    """Requested entity or dependency does not exist."""


class EntityConflictError(ApplicationError):
    """Requested mutation conflicts with canonical state."""
