type ScrollDocument = Pick<Document, "documentElement" | "body">;

const locks = new WeakMap<ScrollDocument, { owners: Set<symbol>; restore: () => void }>();

/** Keep nested dialogs locked until their last owner closes, in any order. */
export function lockDocumentScroll(doc: ScrollDocument): () => void {
  let lock = locks.get(doc);
  if (!lock) {
    const elements = [doc.documentElement, doc.body];
    const previous = elements.map(element => element.style.overflow);
    elements.forEach(element => { element.style.overflow = "hidden"; });
    lock = {
      owners: new Set(),
      restore: () => elements.forEach((element, index) => { element.style.overflow = previous[index]; }),
    };
    locks.set(doc, lock);
  }
  const owner = Symbol();
  lock.owners.add(owner);
  return () => {
    if (!lock.owners.delete(owner) || lock.owners.size) return;
    lock.restore();
    locks.delete(doc);
  };
}
