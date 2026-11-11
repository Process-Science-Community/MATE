"use client";

import { createContext, useContext } from "react";

/**
 * True when the card renders on a board opened through a share. The viewer may
 * read the owner's results, but every non-GET module route 403s for them
 * (`READ_ONLY_DETAIL` in the API loader), so a widget must hide its run/save
 * buttons. False outside a card (a module panel), where the user owns the log.
 */
const CardReadOnlyContext = createContext(false);

export const CardReadOnlyProvider = CardReadOnlyContext.Provider;

export function useCardReadOnly(): boolean {
  return useContext(CardReadOnlyContext);
}
