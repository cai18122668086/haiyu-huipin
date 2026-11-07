import { AppShell } from "@/components/app-shell";
import { ToolDirectory } from "@/components/tool-directory";
import PlatformCaseReviewPage from "@/components/platform-case-review";

export const metadata = {
  title: "首页",
};

export default async function Home({ searchParams }: { searchParams: Promise<{ preview?: string }> }) {
  const params = await searchParams;
  if (process.env.NODE_ENV === "development" && params.preview === "platform-cases") return <PlatformCaseReviewPage />;
  return (
    <AppShell sidebar={false}>
      <div className="tool-market-page">
        <ToolDirectory />
      </div>
    </AppShell>
  );
}
