import { seoMetadata } from "@/lib/seo/metadata";
import { SiteStructuredData } from "@/components/seo-structured-data";
import { AppShell } from "@/components/app-shell";
import { ToolDirectory } from "@/components/tool-directory";

export const metadata = seoMetadata("/");
export const revalidate = 86400;

export default function Home() {
  return (
    <AppShell sidebar={false}>
      <div className="tool-market-page">
        <SiteStructuredData />
        <ToolDirectory />
      </div>
    </AppShell>
  );
}
