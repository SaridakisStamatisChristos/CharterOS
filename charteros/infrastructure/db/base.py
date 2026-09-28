from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for persistence mappings.

    Domain objects should not inherit from this class. ORM mappings remain an infrastructure concern.
    """
