"use client";

import {
  Brush,
  Eraser,
  RotateCcw,
  Square,
  Trash2,
} from "lucide-react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";

import { IMAGE_UPLOAD_FORMAT_HINT } from "@/lib/image-upload";
import {
  assertEditableMask,
  createBinaryMask,
  mapDisplayPointToRaster,
} from "@/lib/mask/mask-utils";
import styles from "./mask-editor.module.css";

export type MaskEditorHandle = {
  exportMaskFile: () => Promise<File>;
  clear: () => void;
};

type MaskEditorProps = {
  sourceUrl?: string;
  sourceName?: string;
  onChange?: (hasSelection: boolean) => void;
  onError?: (message?: string) => void;
};

function canvasBlob(canvas: HTMLCanvasElement) {
  return new Promise<Blob>((resolve, reject) =>
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("图片编码失败。"))),
      "image/png",
    ),
  );
}

export async function normalizeRasterImage(file: File): Promise<File> {
  const sourceUrl = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.decoding = "async";
    image.src = sourceUrl;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器无法创建原图画布。");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await canvasBlob(canvas);
    const basename = file.name.replace(/\.[^.]+$/, "") || "source";
    return new File([blob], `${basename}-normalized.png`, { type: "image/png" });
  } finally {
    URL.revokeObjectURL(sourceUrl);
  }
}

export const MaskEditor = forwardRef<MaskEditorHandle, MaskEditorProps>(
  function MaskEditor({ sourceUrl, sourceName, onChange, onError }, ref) {
    const imageCanvas = useRef<HTMLCanvasElement>(null);
    const maskCanvas = useRef<HTMLCanvasElement>(null);
    const undoSnapshot = useRef<ImageData | null>(null);
    const activePointer = useRef<number | null>(null);
    const previousPoint = useRef<{ x: number; y: number } | null>(null);
    const rectangleStart = useRef<{ x: number; y: number } | null>(null);
    const [tool, setTool] = useState<"brush" | "eraser" | "rectangle">("brush");
    const [brushSize, setBrushSize] = useState(48);
    const [ready, setReady] = useState(false);
    const [canUndo, setCanUndo] = useState(false);

    const selectionState = () => {
      const canvas = maskCanvas.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) return false;
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
      return createBinaryMask(pixels.data, canvas.width, canvas.height)
        .selectedPixels > 0;
    };

    const announceChange = () => onChange?.(selectionState());

    const clear = () => {
      const canvas = maskCanvas.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) return;
      undoSnapshot.current = context.getImageData(0, 0, canvas.width, canvas.height);
      setCanUndo(selectionState());
      context.clearRect(0, 0, canvas.width, canvas.height);
      onChange?.(false);
      onError?.();
    };

    useImperativeHandle(ref, () => ({
      clear,
      async exportMaskFile() {
        const canvas = maskCanvas.current;
        const context = canvas?.getContext("2d");
        if (!canvas || !context || !ready) {
          throw new Error("原图尚未完成解码。");
        }
        const overlay = context.getImageData(0, 0, canvas.width, canvas.height);
        const binary = createBinaryMask(
          overlay.data,
          canvas.width,
          canvas.height,
        );
        assertEditableMask(binary);
        const output = document.createElement("canvas");
        output.width = canvas.width;
        output.height = canvas.height;
        const outputContext = output.getContext("2d");
        if (!outputContext) throw new Error("浏览器无法创建蒙版画布。");
        outputContext.putImageData(
          new ImageData(
            new Uint8ClampedArray(binary.data),
            canvas.width,
            canvas.height,
          ),
          0,
          0,
        );
        return new File([await canvasBlob(output)], "authority-mask.png", {
          type: "image/png",
        });
      },
    }));

    useEffect(() => {
      setReady(false);
      setCanUndo(false);
      undoSnapshot.current = null;
      onChange?.(false);
      onError?.();
      if (!sourceUrl) return;
      let cancelled = false;
      const image = new Image();
      image.decoding = "async";
      image.src = sourceUrl;
      void image.decode().then(() => {
        if (cancelled) return;
        const base = imageCanvas.current;
        const mask = maskCanvas.current;
        const baseContext = base?.getContext("2d");
        const maskContext = mask?.getContext("2d");
        if (!base || !mask || !baseContext || !maskContext) {
          onError?.("浏览器无法初始化图片画布。");
          return;
        }
        base.width = mask.width = image.naturalWidth;
        base.height = mask.height = image.naturalHeight;
        baseContext.drawImage(image, 0, 0, base.width, base.height);
        maskContext.clearRect(0, 0, mask.width, mask.height);
        setReady(true);
      }).catch(() => onError?.(`无法解码这张图片。${IMAGE_UPLOAD_FORMAT_HINT}。`));
      return () => {
        cancelled = true;
      };
    }, [sourceUrl, onChange, onError]);

    const pointFor = (event: ReactPointerEvent<HTMLCanvasElement>) => {
      const canvas = maskCanvas.current!;
      return mapDisplayPointToRaster(
        event.clientX,
        event.clientY,
        canvas.getBoundingClientRect(),
        canvas,
      );
    };

    const drawTo = (point: { x: number; y: number }) => {
      const canvas = maskCanvas.current!;
      const context = canvas.getContext("2d")!;
      const previous = previousPoint.current ?? point;
      context.save();
      context.globalCompositeOperation =
        tool === "eraser" ? "destination-out" : "source-over";
      context.strokeStyle = "rgba(124, 58, 237, 0.56)";
      context.fillStyle = "rgba(124, 58, 237, 0.56)";
      context.lineWidth = brushSize;
      context.lineCap = "round";
      context.lineJoin = "round";
      context.beginPath();
      context.moveTo(previous.x, previous.y);
      context.lineTo(point.x, point.y);
      context.stroke();
      context.beginPath();
      context.arc(point.x, point.y, brushSize / 2, 0, Math.PI * 2);
      context.fill();
      context.restore();
      previousPoint.current = point;
    };

    const drawRectangleTo = (point: { x: number; y: number }) => {
      const canvas = maskCanvas.current!;
      const context = canvas.getContext("2d")!;
      const start = rectangleStart.current;
      const snapshot = undoSnapshot.current;
      if (!start || !snapshot) return;
      context.putImageData(snapshot, 0, 0);
      const left = Math.floor(Math.min(start.x, point.x));
      const top = Math.floor(Math.min(start.y, point.y));
      const width = Math.max(1, Math.ceil(Math.abs(point.x - start.x)));
      const height = Math.max(1, Math.ceil(Math.abs(point.y - start.y)));
      context.save();
      context.globalCompositeOperation = "source-over";
      context.fillStyle = "rgba(124, 58, 237, 0.56)";
      context.fillRect(left, top, width, height);
      context.restore();
    };

    const pointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
      if (!ready || activePointer.current !== null) return;
      const canvas = maskCanvas.current!;
      const context = canvas.getContext("2d")!;
      const point = pointFor(event);
      activePointer.current = event.pointerId;
      canvas.setPointerCapture(event.pointerId);
      undoSnapshot.current = context.getImageData(0, 0, canvas.width, canvas.height);
      setCanUndo(true);
      if (tool === "rectangle") {
        rectangleStart.current = point;
        drawRectangleTo(point);
      } else {
        previousPoint.current = point;
        drawTo(point);
      }
      event.preventDefault();
    };

    const pointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
      if (activePointer.current !== event.pointerId) return;
      const point = pointFor(event);
      if (tool === "rectangle") drawRectangleTo(point);
      else drawTo(point);
      event.preventDefault();
    };

    const pointerEnd = (event: ReactPointerEvent<HTMLCanvasElement>) => {
      if (activePointer.current !== event.pointerId) return;
      activePointer.current = null;
      previousPoint.current = null;
      rectangleStart.current = null;
      if (maskCanvas.current?.hasPointerCapture(event.pointerId)) {
        maskCanvas.current.releasePointerCapture(event.pointerId);
      }
      announceChange();
    };

    const undo = () => {
      const canvas = maskCanvas.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context || !undoSnapshot.current) return;
      context.putImageData(undoSnapshot.current, 0, 0);
      undoSnapshot.current = null;
      setCanUndo(false);
      announceChange();
      onError?.();
    };

    return (
      <div className={`raster-mask-editor ${styles.editor}`}>
        <div className="card-toolbar">
          <div className="toolbar-group" aria-label="蒙版工具">
            <button
              type="button"
              className={tool === "brush" ? "active" : ""}
              onClick={() => setTool("brush")}
              disabled={!ready}
              aria-label="画笔"
              aria-pressed={tool === "brush"}
            >
              <Brush size={17} />
            </button>
            <button
              type="button"
              className={tool === "eraser" ? "active" : ""}
              onClick={() => setTool("eraser")}
              disabled={!ready}
              aria-label="橡皮擦"
              aria-pressed={tool === "eraser"}
            >
              <Eraser size={17} />
            </button>
            <button
              type="button"
              className={tool === "rectangle" ? "active" : ""}
              onClick={() => setTool("rectangle")}
              disabled={!ready}
              aria-label="矩形框选"
              aria-pressed={tool === "rectangle"}
            >
              <Square size={17} />
            </button>
          </div>
          <label className="brush-control">
            笔刷大小
            <input
              type="range"
              min="4"
              max="240"
              value={brushSize}
              onChange={(event) => setBrushSize(Number(event.target.value))}
              disabled={!ready || tool === "rectangle"}
              aria-label="笔刷大小"
            />
            <b>{brushSize}px</b>
          </label>
          <strong>{sourceName ?? "等待上传原图"}</strong>
          <div className="toolbar-group toolbar-group--right">
            <button type="button" onClick={undo} disabled={!canUndo} aria-label="撤销上一步">
              <RotateCcw size={16} />
            </button>
            <button type="button" onClick={clear} disabled={!ready} aria-label="清空选区">
              <Trash2 size={16} />
            </button>
          </div>
        </div>
        <div className={`image-editor ${styles.viewport}`}>
          {sourceUrl ? (
            <div className={`raster-canvas-stack tool-${tool} ${styles.stack}`}>
              <canvas ref={imageCanvas} aria-hidden="true" />
              <canvas
                ref={maskCanvas}
                onPointerDown={pointerDown}
                onPointerMove={pointerMove}
                onPointerUp={pointerEnd}
                onPointerCancel={pointerEnd}
                aria-label="在原图上绘制可编辑区域"
              />
            </div>
          ) : (
            <div className={styles.emptyState}>
              <Brush size={30} />
              <h3>先上传商品原图</h3>
              <p>上传后用画笔涂抹允许修改的真实像素区域。</p>
            </div>
          )}
          <div className={`canvas-status ${styles.status}`}>
            <span className="safe">紫色覆盖区域可编辑；蒙版外像素由服务端锁定。</span>
          </div>
        </div>
      </div>
    );
  },
);
