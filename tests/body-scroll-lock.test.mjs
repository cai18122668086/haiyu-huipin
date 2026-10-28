import assert from "node:assert/strict";
import test from "node:test";
import { lockDocumentScroll } from "../lib/body-scroll-lock.ts";

const page = () => ({ documentElement: { style: { overflow: "" } }, body: { style: { overflow: "" } } });

for (const order of [[0, 1], [1, 0]]) {
  test(`overlapping dialogs release scroll in order ${order}`, () => {
    const doc = page();
    const release = [lockDocumentScroll(doc), lockDocumentScroll(doc)];
    release[order[0]]();
    assert.equal(doc.documentElement.style.overflow, "hidden");
    assert.equal(doc.body.style.overflow, "hidden");
    release[order[1]]();
    assert.equal(doc.documentElement.style.overflow, "");
    assert.equal(doc.body.style.overflow, "");
    release[order[0]]();
    assert.equal(doc.body.style.overflow, "");
  });
}

test("route cleanup restores previous styles and a subsequent dialog can lock again", () => {
  const doc = page();
  doc.documentElement.style.overflow = "clip visible";
  const close = lockDocumentScroll(doc);
  close();
  assert.equal(doc.documentElement.style.overflow, "clip visible");
  const closeNext = lockDocumentScroll(doc);
  assert.equal(doc.documentElement.style.overflow, "hidden");
  closeNext();
  assert.equal(doc.body.style.overflow, "");
  assert.equal(doc.documentElement.style.overflow, "clip visible");
});
