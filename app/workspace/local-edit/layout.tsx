import { seoMetadata } from "@/lib/seo/metadata";
export const metadata = seoMetadata("/workspace/local-edit");
// Initial HTML is the same public presentation for every visitor; personal data
// is fetched only after the client confirms the session.
export const revalidate = 86400;
export default function PublicPageLayout({ children }: { children: React.ReactNode }) {
  return children;
}
