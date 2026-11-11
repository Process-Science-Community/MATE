import { FlowEditor } from "@/components/flows/flow-editor";

export default async function FlowPage({ params }: { params: Promise<{ flowId: string }> }) {
  const { flowId } = await params;
  // Keyed: switching flows remounts the editor, so its local graph re-hydrates and
  // the unmount flush saves the previous one.
  return <FlowEditor key={flowId} flowId={flowId} />;
}
