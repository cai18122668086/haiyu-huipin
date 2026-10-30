import assert from "node:assert/strict";
import test from "node:test";
import { productImageInputIssue } from "../lib/product-image-input-guide.ts";

test("try-on requires both clothing and the wearer, while model-image can create a model", () => {
  assert.match(productImageInputIssue("try_on", 1, 0), /目标模特图/);
  assert.equal(productImageInputIssue("try_on", 1, 1), undefined);
  assert.equal(productImageInputIssue("model_image", 1, 0), undefined);
});

test("a style or model reference alone never substitutes for the authoritative product image", () => {
  for (const preset of ["scene_main", "white_background", "model_image", "change_model", "try_on", "change_scene", "change_background", "change_color"]) {
    assert.ok(productImageInputIssue(preset, 0, 1));
  }
});

test("background, scene, color and model replacement can use a description without a reference image", () => {
  for (const preset of ["change_background", "change_scene", "change_color", "change_model", "white_background", "scene_main"]) {
    assert.equal(productImageInputIssue(preset, 1, 0), undefined);
  }
});
