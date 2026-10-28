import { AppShell } from "@/components/app-shell";
import { CommerceCrawlerWorkspace } from "@/components/commerce-crawler-workspace";

export const metadata = { title: "真实商品爬虫" };

export default function CommerceCrawlerPage() {
  return <AppShell><CommerceCrawlerWorkspace /></AppShell>;
}
