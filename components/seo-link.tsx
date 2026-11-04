"use client";
import NextLink from "next/link";
import { forwardRef, type ComponentPropsWithoutRef } from "react";
import { linkSeoAttributes } from "@/lib/seo/links";

// Centralise private-link hints, including dynamically constructed account and
// task URLs. Native permission checks remain independent of these attributes.
const SeoLink = forwardRef<HTMLAnchorElement, ComponentPropsWithoutRef<typeof NextLink>>(
  function SeoLink({ href, rel, title, ...props }, ref) {
    const target = typeof href === "string" ? href : href.pathname ?? "";
    return <NextLink {...props} {...linkSeoAttributes(target, rel, title)} href={href} ref={ref} />;
  },
);
export default SeoLink;
