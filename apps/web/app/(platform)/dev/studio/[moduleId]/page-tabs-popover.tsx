"use client";

import { useState } from "react";
import { Layers, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { DashboardItem } from "@/lib/dashboard-queries";
import type { LayoutTab, ModuleLayout } from "@/lib/dev-queries";

/** "Panel" is the shell's own tab id for a module's TSX panel. */
const RESERVED = new Set(["panel"]);

function slugId(title: string, taken: Set<string>): string {
  const base =
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 36) || "tab";
  let id = base;
  for (let n = 2; taken.has(id) || RESERVED.has(id); n++) id = `${base}-${n}`;
  return id;
}

function withoutTab(it: DashboardItem): DashboardItem {
  const { tab: _tab, ...rest } = it;
  return rest;
}

/** Studio toolbar: the layout's page tabs (add, rename, delete). */
export function PageTabsPopover({
  layout,
  onChange,
}: {
  layout: ModuleLayout;
  onChange: (next: ModuleLayout) => void;
}) {
  const [title, setTitle] = useState("");
  const tabs = layout.tabs ?? [];

  const setTabs = (next: LayoutTab[], items = layout.items) => {
    const { tabs: _old, ...rest } = layout;
    onChange(next.length ? { ...rest, tabs: next, items } : { ...rest, items: items.map(withoutTab) });
  };

  const add = () => {
    const t = title.trim().slice(0, 60);
    if (!t) return;
    // A single-page layout becomes "Overview" (holding its cards) + the new tab.
    const base = tabs.length ? tabs : [{ id: "overview", title: "Overview" }];
    setTabs([...base, { id: slugId(t, new Set(base.map((x) => x.id))), title: t }]);
    setTitle("");
  };

  // Cards on the deleted tab move to the first remaining one (no `tab` = first).
  const remove = (id: string) =>
    setTabs(
      tabs.filter((t) => t.id !== id),
      layout.items.map((it) => (it.tab === id ? withoutTab(it) : it)),
    );

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" size="sm">
          <Layers className="mr-1.5 h-3.5 w-3.5" />
          Tabs{tabs.length ? ` (${tabs.length})` : ""}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 space-y-3">
        <p className="text-xs text-muted-foreground">
          Tabs split the module page. Cards without a tab sit on the first one.
        </p>
        {tabs.map((t) => (
          <div key={t.id} className="flex items-center gap-2">
            <Input
              aria-label={`Title of tab ${t.id}`}
              className="h-8 text-xs"
              defaultValue={t.title}
              maxLength={60}
              // Commit per keystroke: Escape closes the popover without a blur.
              onChange={(e) => {
                const v = e.target.value.trim();
                if (v && v !== t.title) setTabs(tabs.map((x) => (x.id === t.id ? { ...x, title: v } : x)));
              }}
              onBlur={(e) => {
                if (!e.target.value.trim()) e.target.value = t.title;
              }}
            />
            <span className="w-20 truncate font-mono text-[10px] text-muted-foreground">{t.id}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7 shrink-0"
              aria-label={`Delete tab ${t.title}`}
              onClick={() => remove(t.id)}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        ))}
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <Input
            aria-label="New tab title"
            placeholder="New tab title"
            className="h-8 text-xs"
            value={title}
            maxLength={60}
            onChange={(e) => setTitle(e.target.value)}
          />
          <Button type="submit" size="sm" variant="secondary" disabled={!title.trim()}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
