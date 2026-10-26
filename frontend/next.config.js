/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
import "./src/env.js";

/** @type {import("next").NextConfig} */
const config = {
  // The household's names (frontend/.env HOUSEHOLD_MEMBERS) for the pages; see src/lib/household.ts.
  env: { NEXT_PUBLIC_HOUSEHOLD_MEMBERS: process.env.HOUSEHOLD_MEMBERS ?? "" },
};

export default config;
