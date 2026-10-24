"use client";

import { create } from "zustand";

import { useUi } from "@/lib/stores/ui";

// Open/closed state of the topbar's "How to use" panel. Deliberately NOT part of
// `useUi`: that store is server-persisted per user, and a panel that reopens
// itself on the next visit would be a surprise. This one lives for the page
// session only.
//
// The panel shares the right-hand column with MATE AI, so opening one closes the
// other. Opening the guide closes MATE AI here; the panel closes itself when MATE
// AI opens (see `ModuleGuidePanel`).

interface GuidePanelState {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

export const useGuidePanel = create<GuidePanelState>((set, get) => ({
  open: false,
  setOpen: (open) => {
    if (open) useUi.getState().setMateOpen(false);
    set({ open });
  },
  toggle: () => get().setOpen(!get().open),
}));
