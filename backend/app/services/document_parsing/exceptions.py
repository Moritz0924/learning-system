class DocumentParsingError(Exception):
    error_code = "document_parsing_error"


class UnsupportedDocumentTypeError(DocumentParsingError):
    error_code = "unsupported_document_type"


class FileTypeMismatchError(DocumentParsingError):
    error_code = "file_type_mismatch"


class DocumentTooLargeError(DocumentParsingError):
    error_code = "document_too_large"


class CorruptedDocumentError(DocumentParsingError):
    error_code = "corrupted_document"


class EncryptedPDFError(DocumentParsingError):
    error_code = "encrypted_pdf"


class UnsafeArchiveError(DocumentParsingError):
    error_code = "unsafe_archive"
