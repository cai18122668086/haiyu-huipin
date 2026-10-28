import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../app/workspace/detail-pack/page.tsx", import.meta.url), "utf8");
const expression = page.match(/const nativeReviewed = ([^;]+);/)[1];
const evaluate = new Function("job", "jobId", "isNativeDetail", "nativeApproval", "approvalDocument", "reviewedApprovalKey", "approvalKey", `return (${expression});`);
const job = { data: { id: "historical", plan: { validation_status: "valid" } } };
const oldDocument = "Complete original approval document";
const key = "historical:1:original-document-digest";

test("the original historical document can be acknowledged without new native artifacts", () => {
  assert.equal(evaluate(job, "historical", false, undefined, oldDocument, key, key), true);
  assert.equal(evaluate(job, "historical", false, undefined, oldDocument, "", key), false);
});

test("historical acknowledgment cannot carry across another task, document or invalid plan", () => {
  assert.equal(evaluate(job, "another-job", false, undefined, oldDocument, key, key), false);
  assert.equal(evaluate(job, "historical", false, undefined, oldDocument, key, `${key}-changed`), false);
  assert.equal(evaluate(job, "historical", false, undefined, "", key, key), false);
  assert.equal(evaluate({ data: { ...job.data, plan: { validation_status: "invalid" } } }, "historical", false, undefined, oldDocument, key, key), false);
});

test("current native approval still requires its original artifact protocol", () => {
  assert.equal(evaluate(job, "historical", true, undefined, oldDocument, key, key), false);
  assert.equal(evaluate(job, "historical", true, { version: "old", artifact_id: "artifact" }, oldDocument, key, key), false);
  assert.equal(evaluate(job, "historical", true, { version: "domestic-native-approval-v1" }, oldDocument, key, key), false);
  assert.equal(evaluate(job, "historical", true, { version: "domestic-native-approval-v1", artifact_id: "artifact" }, oldDocument, key, key), true);
});
