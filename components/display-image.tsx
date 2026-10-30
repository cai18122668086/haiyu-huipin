import type { ComponentProps } from "react";
import manifest from "@/lib/display-images.json";

const images: Record<string, {src:string;srcSet:string}> = manifest;

/** Only registered public showcase art has derivatives. User assets and
 * download URLs always retain the original source and bytes. */
export function DisplayImage({src, alt = "", sizes = "(max-width: 640px) 50vw, (max-width: 1199px) 33vw, 480px", srcSet, ...props}: Omit<ComponentProps<"img">,"src"> & {src:string}) {
  const display = images[src];
  return <img {...props} alt={alt} src={display?.src ?? src} srcSet={srcSet ?? display?.srcSet} sizes={display ? sizes : undefined} decoding={props.decoding ?? "async"}/>;
}
