export type DisplayRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

export type RasterSize = {
  width: number;
  height: number;
};

export function mapDisplayPointToRaster(
  clientX: number,
  clientY: number,
  rect: DisplayRect,
  raster: RasterSize,
) {
  if (rect.width <= 0 || rect.height <= 0) {
    throw new Error("画布显示尺寸无效。");
  }
  return {
    x: Math.min(
      raster.width,
      Math.max(0, ((clientX - rect.left) / rect.width) * raster.width),
    ),
    y: Math.min(
      raster.height,
      Math.max(0, ((clientY - rect.top) / rect.height) * raster.height),
    ),
  };
}

export type BinaryMask = {
  data: Uint8ClampedArray;
  selectedPixels: number;
  totalPixels: number;
};

export function createBinaryMask(
  rgba: ArrayLike<number>,
  width: number,
  height: number,
): BinaryMask {
  const totalPixels = width * height;
  if (rgba.length !== totalPixels * 4) {
    throw new Error("蒙版像素数量与画布尺寸不一致。");
  }
  const data = new Uint8ClampedArray(rgba.length);
  let selectedPixels = 0;
  for (let offset = 0; offset < rgba.length; offset += 4) {
    const selected = rgba[offset + 3] > 0;
    const value = selected ? 255 : 0;
    data[offset] = value;
    data[offset + 1] = value;
    data[offset + 2] = value;
    data[offset + 3] = 255;
    if (selected) selectedPixels += 1;
  }
  return { data, selectedPixels, totalPixels };
}

export function assertEditableMask(mask: Pick<BinaryMask, "selectedPixels" | "totalPixels">) {
  if (mask.selectedPixels === 0) {
    throw new Error("选区为空，请先在图片上涂抹需要修改的区域。");
  }
  if (mask.selectedPixels === mask.totalPixels) {
    throw new Error("不能授权整张图片，请保留至少一部分锁定区域。");
  }
}
