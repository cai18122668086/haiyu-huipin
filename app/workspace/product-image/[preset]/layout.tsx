import { notFound } from "next/navigation";
import { getToolSeoPage } from "@/lib/seo/pages";
import { seoMetadata } from "@/lib/seo/metadata";
export const revalidate = 86400;
export async function generateMetadata({ params }: { params: Promise<{ preset: string }> }) {
  const { preset } = await params;
  const pathname = `/workspace/product-image/${preset}`;
  if (!getToolSeoPage(pathname)) notFound();
  return seoMetadata(pathname);
}
export default function ProductToolLayout({ children }: { children: React.ReactNode }) {
  return children;
}

