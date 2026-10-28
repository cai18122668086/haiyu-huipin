"use client";

import { useEffect } from "react";
import { lockDocumentScroll } from "@/lib/body-scroll-lock";

export function useBodyScrollLock(active: boolean) {
  useEffect(() => {
    if (active) return lockDocumentScroll(document);
  }, [active]);
}
