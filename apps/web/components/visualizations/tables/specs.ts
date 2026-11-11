/** Catalog group "Tables & lists": table-shape list vizzes. */

import type { WidgetPropSchema } from "@/lib/dashboard-queries";
import type { ColumnType, VizSpec } from "@/lib/visualizations/types";

import { RankedListViz } from "./ranked-list";
import { TableViz } from "./table-viz";

const NUM: ColumnType[] = ["number", "integer", "duration"];
const CAT: ColumnType[] = ["string", "boolean", "enum", "datetime"];

/** A column-picker option: the settings form fills its choices from the bound
 * dataset (`ui.enumFrom`). The vizzes also accept "a, b" typed as text. */
export function columnOption(
  title: string,
  enumFrom: "columns" | "numericColumns",
  multiple = true,
  description?: string,
): WidgetPropSchema {
  return { type: multiple ? "array" : "string", title, description, ui: { enumFrom } };
}

export const VALUE_FORMAT: WidgetPropSchema = {
  type: "string",
  title: "Value format",
  enum: ["number", "integer", "percent", "percent100", "duration", "datetime", "text"],
  enumLabels: ["Number", "Integer", "Percent (0–1)", "Percent (0–100)", "Duration (s)", "Date/time", "Text"],
};

export const tableSpecs: VizSpec[] = [
  {
    id: "table",
    title: "Table",
    icon: "Table",
    group: "table",
    description: "Sortable, searchable table with formatted columns.",
    needs: "A table dataset: any route that returns a list of row objects.",
    example: {
      rows: [
        { activity: "Register", cases: 120, share: 0.48, mean_duration_s: 3600, path: ["Register", "Check"] },
        { activity: "Check", cases: 95, share: 0.38, mean_duration_s: 7200, path: ["Check", "Approve"] },
        { activity: "Approve", cases: 35, share: 0.14, mean_duration_s: 1800, path: ["Approve"] },
      ],
    },
    accepts: ["table"],
    fields: [],
    options: {
      properties: {
        search: { type: "boolean", title: "Search box", default: false },
        page_size: {
          type: "integer",
          title: "Rows per page",
          description: "0 shows every row.",
          default: 50,
          minimum: 0,
          maximum: 1000,
        },
        percent_columns: columnOption("Show as percent", "numericColumns"),
        datetime_columns: columnOption("Show as date/time", "columns"),
        data_bars: { type: "boolean", title: "Data bars on all numeric columns", default: false },
        bar_columns: columnOption("Data bars on", "numericColumns"),
        chip_columns: columnOption("Show as chips", "columns", true, "Lists always show as A → B chips."),
        link_column: columnOption("Link column", "columns", false),
        link_template: {
          type: "string",
          title: "Link template",
          description: "URL with {value}, e.g. /logs/x?activity={value}. Empty: the cell is the URL.",
        },
      },
    },
    presets: [
      {
        id: "table-databars",
        title: "Table with data bars",
        description: "Numeric columns get an in-cell bar scaled to the column max.",
        icon: "TableProperties",
        options: { data_bars: true, search: true },
      },
    ],
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: TableViz,
  },
  {
    id: "ranked-list",
    title: "Ranked list",
    icon: "ListOrdered",
    group: "table",
    description: "Top items with value and share bar; long labels stay readable.",
    needs: "A table dataset with a label column and, optionally, a numeric value column (else rows are counted).",
    example: {
      items: [
        { activity: "Register", cases: 120, owner: "Front office" },
        { activity: "Check", cases: 95, owner: "Back office" },
        { activity: "Approve", cases: 35, owner: "Manager" },
      ],
    },
    accepts: ["table"],
    fields: [
      { key: "label", label: "Label", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM },
      { key: "secondary", label: "Caption", accepts: [...CAT, ...NUM] },
    ],
    options: {
      properties: {
        top_n: { type: "integer", title: "Show top N", description: "0 shows all.", default: 10, minimum: 0 },
        sort: {
          type: "string",
          title: "Sort",
          enum: ["desc", "asc", "none"],
          enumLabels: ["Largest first", "Smallest first", "As in data"],
          default: "desc",
        },
        show_bar: { type: "boolean", title: "Share bar", default: true },
        value_format: VALUE_FORMAT,
        layout: {
          type: "string",
          title: "Layout",
          enum: ["list", "chips"],
          enumLabels: ["List", "Chips"],
          default: "list",
          ui: { widget: "segmented" },
        },
      },
    },
    presets: [
      {
        id: "variant-chips",
        title: "Variant list",
        description: "Activity sequences as A → B chips with their case counts.",
        icon: "GitBranch",
        options: { top_n: 20, value_format: "integer" },
      },
      {
        id: "chips",
        title: "Category chips",
        description: "Compact wrapped chips with a count per category.",
        icon: "Tags",
        options: { layout: "chips", top_n: 0 },
      },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 4 },
    Component: RankedListViz,
  },
];
