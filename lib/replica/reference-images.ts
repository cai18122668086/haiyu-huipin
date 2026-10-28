export type ReferenceImageStatus = "ready" | "blank" | "unavailable";

type ProbeImage = Pick<HTMLImageElement, "src" | "referrerPolicy" | "onload" | "onerror" | "naturalWidth" | "naturalHeight">;

// A network/CSP failure says nothing about the actual image dimensions.
// The server import can still download it when a browser preview is unavailable.
export function probeReferenceImage(src: string, options: { createImage?: () => ProbeImage; timeoutMs?: number } = {}): Promise<ReferenceImageStatus> {
  return new Promise(resolve => {
    const image = options.createImage?.() ?? new Image();
    const finish = (status: ReferenceImageStatus) => {
      clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
      resolve(status);
    };
    const timer = setTimeout(() => finish("unavailable"), options.timeoutMs ?? 15000);
    image.referrerPolicy = "no-referrer";
    image.onload = () => finish(image.naturalWidth >= 32 && image.naturalHeight >= 32 ? "ready" : "blank");
    image.onerror = () => finish("unavailable");
    image.src = src;
  });
}
