import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Public ISR pages refresh daily and may be served stale for one further
  // hour. Private layouts explicitly opt out of ISR with force-dynamic.
  expireTime: 90000,
};

export default nextConfig;
