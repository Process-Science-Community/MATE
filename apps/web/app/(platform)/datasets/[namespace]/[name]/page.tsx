import { DatasetDetailClient } from "./dataset-detail-client";

export default async function DatasetDetailPage({
  params,
}: {
  params: Promise<{ namespace: string; name: string }>;
}) {
  const { namespace, name } = await params;
  return <DatasetDetailClient namespace={namespace} name={name} />;
}
