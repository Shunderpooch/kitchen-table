import { MEMBER_A, MEMBER_B } from "~/lib/household";
export type SplitMode = "percentage" | "exact";

export type FriendShareInput = {
  friendId: number;
  amount: number;
};

function roundMoney(value: number) {
  return Math.round(value * 100) / 100;
}

function getAmountSign(value: number) {
  return value < 0 ? -1 : 1;
}

export function normalizeFriendShares(shares: FriendShareInput[]) {
  const grouped = new Map<number, number>();

  for (const share of shares) {
    if (!Number.isFinite(share.friendId) || share.friendId <= 0) {
      continue;
    }

    const amount = roundMoney(share.amount);
    if (!Number.isFinite(amount) || amount <= 0) {
      continue;
    }

    grouped.set(share.friendId, roundMoney((grouped.get(share.friendId) ?? 0) + amount));
  }

  return Array.from(grouped.entries()).map(([friendId, amount]) => ({
    friendId,
    amount,
  }));
}

export function sumFriendShares(shares: FriendShareInput[]) {
  return roundMoney(
    normalizeFriendShares(shares).reduce((total, share) => total + share.amount, 0),
  );
}

export function calculateHouseholdRemainder(amount: number, shares: FriendShareInput[]) {
  const sign = getAmountSign(amount);
  return roundMoney(amount - sign * sumFriendShares(shares));
}

export function summarizeTrackedFriends(
  shares: FriendShareInput[],
  friendsById: Map<number, { name: string }>,
) {
  const names = normalizeFriendShares(shares)
    .map((share) => friendsById.get(share.friendId)?.name?.trim())
    .filter((name): name is string => Boolean(name));

  return names.length > 0 ? names.join(", ") : null;
}

export function calculateSpendAllocation(
  amount: number,
  split: number,
  spender: string | null,
  shares: FriendShareInput[],
) {
  const normalizedSpender = spender?.trim().toLowerCase();
  const householdAmount = calculateHouseholdRemainder(amount, shares);
  const safeSplit = Math.min(Math.max(split, 0), 1);
  const spenderShare = roundMoney(householdAmount * safeSplit);
  const otherShare = roundMoney(householdAmount - spenderShare);

  if (normalizedSpender === MEMBER_A.toLowerCase()) {
    return {
      memberASpend: spenderShare,
      memberBSpend: otherShare,
      householdAmount,
    };
  }

  if (normalizedSpender === MEMBER_B.toLowerCase()) {
    return {
      memberASpend: otherShare,
      memberBSpend: spenderShare,
      householdAmount,
    };
  }

  return {
    memberASpend: null,
    memberBSpend: null,
    householdAmount,
  };
}

export function deriveSplitFromExactAmounts(input: {
  householdAmount: number;
  spender: string | null;
  memberAAmount: number;
  memberBAmount: number;
}) {
  const householdAmount = Math.abs(roundMoney(input.householdAmount));
  const memberAAmount = Math.abs(roundMoney(input.memberAAmount));
  const memberBAmount = Math.abs(roundMoney(input.memberBAmount));

  if (householdAmount <= 0) {
    return 1;
  }

  const normalizedSpender = input.spender?.trim().toLowerCase();
  if (normalizedSpender === MEMBER_A.toLowerCase()) {
    return Math.min(Math.max(memberAAmount / householdAmount, 0), 1);
  }

  if (normalizedSpender === MEMBER_B.toLowerCase()) {
    return Math.min(Math.max(memberBAmount / householdAmount, 0), 1);
  }

  return 1;
}
