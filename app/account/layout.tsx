import type { Metadata } from "next";
export const metadata: Metadata = { robots: { index: false, follow: false } };
export const dynamic = "force-dynamic";
export default function PrivatePageLayout({ children }: { children: React.ReactNode }) {
  return children;
}
