"use client";

import { useEffect } from "react";
import { captureInvitation } from "@/lib/auth/invitation";

export function InvitationCapture() {
  useEffect(() => { captureInvitation(window.location.search); }, []);
  return null;
}
