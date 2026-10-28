export const IMAGE_UPLOAD_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"] as const;

export const IMAGE_UPLOAD_MIME_TYPES = [
  "image/jpeg",
  "image/png",
  "image/webp",
] as const;

export const IMAGE_UPLOAD_ACCEPT = [
  ...IMAGE_UPLOAD_EXTENSIONS,
  ...IMAGE_UPLOAD_MIME_TYPES,
].join(",");

export const IMAGE_UPLOAD_FORMAT_HINT = "支持 JPG/JPEG、PNG 和 WebP 图片";


export function imageUploadLimit(multiple: boolean, maxFiles?: number): number {
  const limit = maxFiles === undefined ? (multiple ? 8 : 1) : maxFiles;
  if (!Number.isFinite(limit)) return 0;
  return Math.max(0, Math.min(multiple ? Infinity : 1, Math.floor(limit)));
}

export function isImageFileDrag(data: { types: readonly string[]; items?: ArrayLike<{ kind: string }> }): boolean {
  return Array.from(data.types).includes("Files") || Array.from(data.items ?? []).some((item) => item.kind === "file");
}

export function validateImageUpload(
  files: readonly File[],
  options: { multiple?: boolean; maxFiles?: number } = {},
): { files: File[]; error?: string } {
  if (!files.length) return { files: [] };
  const limit = imageUploadLimit(options.multiple ?? false, options.maxFiles);
  if (!limit) return { files: [], error: "已达到图片数量上限，请先调整已选素材。" };
  if (files.length > limit) return { files: [], error: `本次最多上传 ${limit} 张图片，请减少数量后重试。` };
  const invalid = files.find((file) => {
    const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0];
    const mime = file.type.toLowerCase();
    const supportedExtension = IMAGE_UPLOAD_EXTENSIONS.some((value) => value === extension);
    const supportedMime = IMAGE_UPLOAD_MIME_TYPES.some((value) => value === mime);
    return supportedMime ? Boolean(extension && !supportedExtension) : !(supportedExtension && (!mime || mime === "application/octet-stream"));
  });
  if (invalid) return { files: [], error: `“${invalid.name}”格式暂不支持。${IMAGE_UPLOAD_FORMAT_HINT}。` };
  return { files: Array.from(files) };
}
