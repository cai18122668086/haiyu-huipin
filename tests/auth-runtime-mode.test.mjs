import assert from "node:assert/strict";
import test from "node:test";
import { getAuthMode } from "../lib/auth/config.ts";

test("production ignores local development authentication flags", () => {
  const previousNodeEnv = process.env.NODE_ENV;
  const previousAuthMode = process.env.NEXT_PUBLIC_AUTH_MODE;
  try {
    process.env.NEXT_PUBLIC_AUTH_MODE = "development";
    process.env.NODE_ENV = "production";
    assert.equal(getAuthMode(), "external");
    process.env.NODE_ENV = "development";
    assert.equal(getAuthMode(), "development");
    process.env.NEXT_PUBLIC_AUTH_MODE = "external";
    assert.equal(getAuthMode(), "external");
  } finally {
    if (previousNodeEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previousNodeEnv;
    if (previousAuthMode === undefined) delete process.env.NEXT_PUBLIC_AUTH_MODE;
    else process.env.NEXT_PUBLIC_AUTH_MODE = previousAuthMode;
  }
});
