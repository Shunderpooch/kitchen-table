/**
 * The household's people (HOUSEHOLD_MEMBERS in frontend/.env, e.g. "Alex,Sam";
 * one or two names). Member A is the first name, member B the second; each
 * expense's household share is split between them (member_a_spend,
 * member_b_spend). next.config.js passes the setting to the browser.
 */
const configured = (process.env.NEXT_PUBLIC_HOUSEHOLD_MEMBERS ?? "")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean)
  .slice(0, 2);

export const MEMBERS: [string, ...string[]] =
  configured.length > 0 ? (configured as [string, ...string[]]) : ["Alex", "Sam"];
export const MEMBER_A = MEMBERS[0];
/** A one-person household still has a member B slot; its share stays empty. */
export const MEMBER_B = MEMBERS[1] ?? "Partner";
export const MEMBER_ENUM: [string, string] = [MEMBER_A, MEMBER_B];

/** "a" / "b" for a spender or card-owner name, matched case-insensitively. */
export function memberSlot(name: string | null | undefined): "a" | "b" | null {
  const normalized = name?.trim().toLowerCase();
  if (!normalized) return null;
  if (normalized === MEMBER_A.toLowerCase()) return "a";
  if (normalized === MEMBER_B.toLowerCase()) return "b";
  return null;
}
