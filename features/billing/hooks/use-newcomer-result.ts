"use client";

import { useEffect, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { markTrialViewed, newcomerOfferKey, useNewcomerOffer, type TrialInteraction } from "@/features/billing/api/growth-center";
import { newcomerResultSource } from "@/lib/newcomer-offer-policy";

export async function recordNewcomerInteraction(cache: QueryClient, workspaceId: string, interaction: TrialInteraction) {
  try {
    const result = await markTrialViewed(workspaceId, interaction);
    // An older response must not undo an impression claimed by this tab.
    cache.setQueryData(newcomerOfferKey(workspaceId), (current: typeof result | undefined) => {
      if (current && Date.parse(current.server_now) > Date.parse(result.server_now)) return current;
      return result;
    });
    return true;
  } catch {
    // Promotions never block viewing, downloading or continuing a generation.
    return false;
  }
}

export function useNewcomerResultImage(workspaceId: string | undefined, jobId: string | undefined) {
  const cache = useQueryClient();
  const query = useNewcomerOffer(jobId ? workspaceId : undefined);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const needed = Boolean(query.data?.eligible && !query.data.trial_viewed);
  useEffect(() => {
    if (!image || !workspaceId || !jobId || !needed) return;
    let active = true, intersecting = false, recorded = false, inFlight = false;
    const observe = async () => {
      if (!active || recorded || inFlight || !intersecting || !image.complete || image.naturalWidth === 0 || document.visibilityState !== "visible") return;
      const rect = image.getBoundingClientRect();
      const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
      const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
      if (document.elementFromPoint(x, y) !== image) return;
      inFlight = true;
      recorded = await recordNewcomerInteraction(cache, workspaceId, newcomerResultSource(jobId));
      inFlight = false;
    };
    const observer = new IntersectionObserver(entries => {
      intersecting = entries.some(entry => entry.isIntersecting);
      void observe();
    }, { threshold: 0.1 });
    observer.observe(image);
    const refresh = () => void observe();
    const afterInteraction = () => requestAnimationFrame(refresh);
    image.addEventListener("load", refresh);
    document.addEventListener("visibilitychange", refresh);
    document.addEventListener("click", afterInteraction);
    document.addEventListener("keydown", afterInteraction);
    const timer = window.setInterval(refresh, 30_000);
    return () => {
      active = false;
      observer.disconnect();
      image.removeEventListener("load", refresh);
      document.removeEventListener("visibilitychange", refresh);
      document.removeEventListener("click", afterInteraction);
      document.removeEventListener("keydown", afterInteraction);
      window.clearInterval(timer);
    };
  }, [image, workspaceId, jobId, needed, cache]);
  return setImage;
}
