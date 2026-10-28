export type ProductImageFilterOptions = {
  /**
   * The API already separates main/detail/SKU arrays. Keep platform asset
   * names such as `property` and `sku` when the caller is using those typed
   * arrays instead of trying to classify them again from their URL.
   */
  preserveClassifiedNames?: boolean;
};

const UI_ASSET_TOKENS = [
  "favicon",
  "sprite",
  "avatar",
  "qrcode",
  "qr-code",
  "loading",
  "placeholder",
  "default-icon",
  "collect-icon",
  "s.gif",
  "atmosphere_center_image",
  "center_image",
  "qnaigc",
];

const CLASSIFICATION_TOKENS = [
  "sku",
  "skuimage",
  "propimg",
  "property",
  "specification",
  "colorprop",
  "sizeprop",
];

export const isProductAsset = (
  src: string,
  options: ProductImageFilterOptions = {},
) => {
  const value = src.toLowerCase();
  if (UI_ASSET_TOKENS.some((token) => value.includes(token))) return false;
  if (
    !options.preserveClassifiedNames &&
    CLASSIFICATION_TOKENS.some((token) => value.includes(token))
  ) {
    return false;
  }

  const match = value.match(
    /(?:-|_)(\d{1,5})[-x](\d{1,5})(?=(?:[._?]|$))/,
  );
  if (!match) return !value.includes("-gg_dtc");
  const width = Number(match[1]);
  const height = Number(match[2]);
  const smaller = Math.min(width, height);
  const larger = Math.max(width, height);
  if (larger <= 180 || smaller <= 48) return false;
  if (value.includes("-tps-") && (smaller < 300 || larger < 500)) return false;
  return !value.includes("-gg_dtc");
};

/**
 * Build a stable identity without discarding the query string. Some CDN
 * responses use one pathname with different query parameters for different
 * product assets; pathname-only deduplication incorrectly collapsed them to
 * one image.
 */
export const imageIdentity = (src: string) => {
  try {
    const parsed = new URL(src);
    const query = [...parsed.searchParams.entries()]
      .sort(([leftKey, leftValue], [rightKey, rightValue]) =>
        `${leftKey}\u0000${leftValue}`.localeCompare(
          `${rightKey}\u0000${rightValue}`,
        ),
      )
      .map(
        ([key, value]) =>
          `${encodeURIComponent(key)}=${encodeURIComponent(value)}`,
      )
      .join("&");
    const pathname = parsed.pathname.toLowerCase().replace(
      /(?:_\d+x\d+(?:q\d+)?|_q\d+)(?:\.[a-z]{2,5})?$/,
      "",
    );
    return `${parsed.hostname.toLowerCase()}${pathname}${query ? `?${query}` : ""}`;
  } catch {
    return src.toLowerCase();
  }
};

export const uniqueProductImages = (
  values: unknown[],
  options: ProductImageFilterOptions = {},
) => {
  const seen = new Set<string>();
  return values
    .filter((value): value is string => {
      if (typeof value !== "string" || !value.trim()) return false;
      // The API has already classified these arrays on the server. Do not
      // apply URL-name or size heuristics a second time: valid 1688 detail
      // assets frequently use CDN names such as ``-gg_dtc`` or omit encoded
      // dimensions, and those heuristics used to erase the returned images.
      if (options.preserveClassifiedNames) return true;
      return isProductAsset(value, options);
    })
    .filter((value) => {
      const identity = imageIdentity(value);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    });
};
