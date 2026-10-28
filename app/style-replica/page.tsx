import { AppShell } from "@/components/app-shell";
import { StyleReplicaWorkspace } from "@/components/style-replica-workspace";
export const metadata = { title: "一键复刻" };
export default function StyleReplicaPage() { return <AppShell flush sidebar={false}><StyleReplicaWorkspace /></AppShell>; }
