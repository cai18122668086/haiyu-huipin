import { AppShell } from "@/components/app-shell";
import { ToolDirectory } from "@/components/tool-directory";

export const metadata = {
  title: "首页",
};

export default function Home() {
  return (
    <AppShell sidebar={false}>
      <div className="tool-market-page">
        <ToolDirectory />
      </div>
    </AppShell>
  );
}
