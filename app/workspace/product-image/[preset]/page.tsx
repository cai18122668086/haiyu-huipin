import { notFound } from "next/navigation";

import { ProductImageWorkspace } from "@/components/product-image-workspace";
import {
  PRODUCT_IMAGE_FEATURES,
  getProductImageFeatureByPresetSlug,
} from "@/lib/feature-registry";

export function generateStaticParams() {
  return PRODUCT_IMAGE_FEATURES.map((feature) => ({ preset: feature.presetSlug }));
}

export default async function ProductImagePresetPage({
  params,
}: {
  params: Promise<{ preset: string }>;
}) {
  const { preset } = await params;
  if (!getProductImageFeatureByPresetSlug(preset)) notFound();
  return <ProductImageWorkspace key={preset} preset={preset} />;
}
