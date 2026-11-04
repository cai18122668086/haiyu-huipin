import { AppShell } from "@/components/app-shell";
import { InspirationGalleryDb } from "@/components/inspiration-gallery-db-v2";


export default function InspirationPage() {
  return (
    <AppShell sidebar={false} flush>
      <InspirationGalleryDb />
    </AppShell>
  );
}
