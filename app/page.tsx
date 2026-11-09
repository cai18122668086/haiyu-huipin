import { seoMetadata } from "@/lib/seo/metadata";
import { SiteStructuredData } from "@/components/seo-structured-data";
import { AppShell } from "@/components/app-shell";
import { ToolDirectory } from "@/components/tool-directory";
import PromotionPreview from "@/components/promotion-preview";
import PlatformCaseReviewPage from "@/components/platform-case-review";

export const metadata = seoMetadata("/");
export const revalidate = 86400;

export default async function Home({ searchParams }: { searchParams: Promise<{ preview?: string }> }) {
  if (process.env.NODE_ENV === "development" && (await searchParams)?.preview === "platform-cases") return <PlatformCaseReviewPage />;
  if (process.env.NODE_ENV === "development" && (await searchParams)?.preview === "promotions") return <PromotionPreview />;
  return (
    <AppShell sidebar={false}>
      <div className="tool-market-page">
        <SiteStructuredData />
        <ToolDirectory />
      </div>
    </AppShell>
  );
}
