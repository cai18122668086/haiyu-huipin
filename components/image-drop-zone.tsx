"use client";

import { Upload } from "lucide-react";
import { useId, useRef, useState, type DragEvent, type ReactNode, type RefObject } from "react";
import { errorMessage } from "@/lib/api/request";
import { IMAGE_UPLOAD_ACCEPT, imageUploadLimit, isImageFileDrag, validateImageUpload } from "@/lib/image-upload";
import styles from "./image-drop-zone.module.css";

type Controls = { openFileDialog: () => void; disabled: boolean; busy: boolean };

export function ImageDropZone({ label, children, onFiles, className, as: Tag = "div", disabled = false, busy = false, multiple = false, maxFiles, inputRef, showHint = true }: {
  label: string;
  children: ReactNode | ((controls: Controls) => ReactNode);
  onFiles: (files: File[]) => void | Promise<void>;
  className?: string;
  as?: "div" | "section";
  disabled?: boolean;
  busy?: boolean;
  multiple?: boolean;
  maxFiles?: number;
  inputRef?: RefObject<HTMLInputElement | null>;
  showHint?: boolean;
}) {
  const ownInput = useRef<HTMLInputElement>(null);
  const picker = inputRef ?? ownInput;
  const depth = useRef(0);
  const selectionLock = useRef(false);
  const [dragging, setDragging] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState<string>();
  const id = useId();
  const limit = imageUploadLimit(multiple, maxFiles);
  const uploading = busy || selecting;
  const blocked = disabled || uploading || limit === 0;

  const resetDrag = () => { depth.current = 0; setDragging(false); };
  const acceptFiles = async (files: File[]) => {
    if (blocked || selectionLock.current) return;
    setError(undefined);
    const selected = validateImageUpload(files, { multiple, maxFiles });
    if (selected.error) { setError(selected.error); return; }
    if (!selected.files.length) return;
    selectionLock.current = true;
    setSelecting(true);
    try { await onFiles(selected.files); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { selectionLock.current = false; setSelecting(false); }
  };
  const dragOver = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = !blocked && isImageFileDrag(event.dataTransfer) ? "copy" : "none";
  };

  return <Tag className={[styles.zone, className].filter(Boolean).join(" ")} role="group" aria-label={`${label}上传区`}
    aria-describedby={error ? `${id}-error` : showHint ? `${id}-hint` : undefined}
    aria-disabled={disabled || uploading || undefined} aria-busy={uploading}
    data-dragging={dragging && !blocked || undefined}
    onDragEnter={(event) => {
      event.preventDefault(); event.stopPropagation();
      if (!blocked && isImageFileDrag(event.dataTransfer)) { depth.current += 1; setDragging(true); }
    }}
    onDragOver={dragOver}
    onDragLeave={(event) => {
      event.preventDefault(); event.stopPropagation();
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    }}
    onDragEnd={resetDrag}
    onDrop={(event) => {
      event.preventDefault(); event.stopPropagation(); resetDrag();
      if (blocked || !isImageFileDrag(event.dataTransfer)) return;
      const files = Array.from(event.dataTransfer.files);
      if (!files.length) { setError("请直接拖入图片文件，暂不支持文件夹。"); return; }
      void acceptFiles(files);
    }}>
    {typeof children === "function" ? children({
      openFileDialog: () => { if (!blocked) document.getElementById(`${id}-input`)?.click(); },
      disabled: blocked,
      busy: uploading,
    }) : children}
    <input id={`${id}-input`} ref={picker} className={styles.hiddenInput} type="file" accept={IMAGE_UPLOAD_ACCEPT}
      multiple={multiple} disabled={blocked} aria-label={`${label}文件选择`}
      onChange={(event) => {
        const files = Array.from(event.currentTarget.files ?? []);
        event.currentTarget.value = "";
        void acceptFiles(files);
      }} />
    {showHint && <p id={`${id}-hint`} className={styles.hint}>{limit ? `拖拽图片到这里，或点击上传 · 本次最多 ${limit} 张` : "已达到数量上限，可从素材库调整选择。"}</p>}
    {error && <p id={`${id}-error`} className={styles.error} role="alert">{error}</p>}
    {dragging && !blocked && <div className={styles.overlay} aria-hidden="true"><Upload size={26} /><span>松开即可上传图片</span></div>}
  </Tag>;
}
