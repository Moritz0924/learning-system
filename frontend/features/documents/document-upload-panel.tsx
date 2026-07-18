"use client";

import { DragEvent, useRef, useState } from "react";
import { MdClose, MdCloudUpload, MdInsertDriveFile } from "react-icons/md";

import { DocumentError } from "./document-error";


const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const SUPPORTED_EXTENSIONS = new Set([
  "pdf", "pptx", "png", "jpg", "jpeg", "webp", "bmp", "tiff", "md", "txt",
]);

export function validateDocumentFile(file: File): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  if (!SUPPORTED_EXTENSIONS.has(extension)) {
    return "不支持此文件类型。请选择 PDF、PPTX、图片、Markdown 或 TXT 文件。";
  }
  if (file.size === 0) return "文件不能为空。";
  if (file.size > MAX_UPLOAD_BYTES) return "文件不能超过 20 MB。";
  return null;
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocumentUploadPanel({
  busy,
  onUpload,
}: {
  busy: boolean;
  onUpload: (file: File) => Promise<boolean>;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [validationError, setValidationError] = useState("");
  const [dragging, setDragging] = useState(false);

  const selectFile = (file?: File) => {
    if (!file) return;
    const error = validateDocumentFile(file);
    setValidationError(error || "");
    setSelectedFile(error ? null : file);
  };

  const clearSelection = () => {
    setSelectedFile(null);
    setValidationError("");
    if (inputRef.current) inputRef.current.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    selectFile(event.dataTransfer.files[0]);
  };

  const submit = async () => {
    if (!selectedFile || busy) return;
    const submittedFile = selectedFile;
    const succeeded = await onUpload(submittedFile);
    if (succeeded) {
      setSelectedFile((current) => (current === submittedFile ? null : current));
      if (inputRef.current && inputRef.current.files?.[0] === submittedFile) {
        inputRef.current.value = "";
      }
    }
  };

  return (
    <div className="space-y-3">
      <label
        data-testid="document-drop-zone"
        htmlFor="document-file-input"
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-5 py-6 text-center ${
          dragging ? "border-teal bg-tealSoft" : "border-line bg-[#fbfdfc] hover:border-teal"
        }`}
      >
        <MdCloudUpload className="text-3xl text-teal" aria-hidden />
        <span className="mt-3 text-sm font-semibold">拖拽文件到这里，或点击选择</span>
        <span className="mt-1 text-xs leading-5 text-muted">PDF、PPTX、常见图片、Markdown、TXT，最大 20 MB</span>
      </label>
      <input
        ref={inputRef}
        data-testid="document-file-input"
        id="document-file-input"
        type="file"
        className="sr-only"
        accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp,.bmp,.tiff,.md,.txt"
        onChange={(event) => selectFile(event.target.files?.[0])}
      />

      {validationError && <DocumentError message={validationError} testId="document-validation-error" />}

      {selectedFile && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-white px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <MdInsertDriveFile className="shrink-0 text-xl text-teal" aria-hidden />
            <div className="min-w-0">
              <div data-testid="selected-document-name" className="truncate text-sm font-semibold">{selectedFile.name}</div>
              <div className="mt-0.5 text-xs text-muted">{formatBytes(selectedFile.size)}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              data-testid="cancel-document-selection"
              className="flex h-9 items-center gap-1 rounded-lg border border-line px-3 text-xs font-semibold text-muted"
              onClick={clearSelection}
              type="button"
            >
              <MdClose aria-hidden /> 取消
            </button>
            <button
              data-testid="upload-selected-document"
              className="h-9 rounded-lg bg-teal px-4 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
              disabled={busy}
              onClick={() => void submit()}
              type="button"
            >
              {busy ? "上传中" : "开始上传"}
            </button>
          </div>
        </div>
      )}

      {!selectedFile && (
        <button data-testid="upload-selected-document" className="sr-only" disabled type="button">
          开始上传
        </button>
      )}
    </div>
  );
}
