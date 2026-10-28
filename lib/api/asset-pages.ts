type AssetPage<Item extends { id: string }> = {
  items: Item[];
  has_more: boolean;
  next_cursor: string | null;
  total?: number | null;
};

// Material pickers need older uploads as well as the first API page.
export async function collectAssetPages<Item extends { id: string }>(
  fetchPage: (cursor?: string) => Promise<AssetPage<Item>>,
): Promise<AssetPage<Item>> {
  const items = new Map<string, Item>();
  const cursors = new Set<string>();
  let cursor: string | undefined;
  let total: number | null | undefined;

  while (true) {
    const page = await fetchPage(cursor);
    total ??= page.total;
    for (const item of page.items) items.set(item.id, item);
    if (!page.has_more) {
      return { items: [...items.values()], total: total ?? items.size, has_more: false, next_cursor: null };
    }
    if (!page.next_cursor || cursors.has(page.next_cursor)) {
      throw new Error("素材列表分页异常，请重新连接后再试。");
    }
    cursors.add(page.next_cursor);
    cursor = page.next_cursor;
  }
}
