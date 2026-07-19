from .models import DocumentParseResult

__all__ = ["DocumentParseResult", "DocumentParser"]


def __getattr__(name: str):
    if name == "DocumentParser":
        from .parser import DocumentParser

        return DocumentParser
    raise AttributeError(name)
