type CursorPage<Item extends { id: string }> = {
  items: Item[];
  has_more: boolean;
  next_cursor: string | null;
  total?: number | null;
};

export async function collectCursorPages<Item extends { id: string }>(
  fetchPage: (cursor?: string) => Promise<CursorPage<Item>>,
  errorMessage: string,
): Promise<CursorPage<Item>> {
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
    if (!page.next_cursor || cursors.has(page.next_cursor)) throw new Error(errorMessage);
    cursors.add(page.next_cursor);
    cursor = page.next_cursor;
  }
}
