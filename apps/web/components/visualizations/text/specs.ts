/** Catalog group "Text & status": text/status vizzes. */

import type { VizSpec } from "@/lib/visualizations/types";

import { columnOption } from "../tables/specs";
import { CalloutViz } from "./callout";

export const textSpecs: VizSpec[] = [
  {
    id: "callout",
    title: "Callout",
    icon: "MessageSquareText",
    group: "text",
    description: "Status or summary text with {field} values from the data.",
    needs: "A table (first row), KPI or text dataset; {field} in the text is replaced by that column, measure or {value}.",
    example: {
      rows: [
        {
          summary: "Fitness is below target on 2 activities.",
          fitness: 0.82,
          missing: ["Archive", "Notify"],
        },
      ],
    },
    accepts: ["table", "kpi", "blob"],
    fields: [],
    options: {
      properties: {
        tone: {
          type: "string",
          title: "Tone",
          enum: ["info", "success", "warning", "danger", "none"],
          enumLabels: ["Info", "Success", "Warning", "Danger", "Plain text"],
          default: "info",
        },
        text: {
          type: "string",
          title: "Text (markdown)",
          description: "Use {column} or {measure_key}; empty shows the data's first text value.",
          ui: { widget: "textarea" },
        },
        list_field: columnOption("Chips from list column", "columns", false),
        hide_when_empty: { type: "boolean", title: "Hide when empty", default: false },
      },
    },
    presets: [
      {
        id: "data-text",
        title: "Text from data",
        description: "Plain markdown text taken from a dataset field.",
        icon: "Type",
        options: { tone: "none" },
      },
    ],
    defaults: { w: 4, h: 6, minW: 2, minH: 2 },
    Component: CalloutViz,
  },
];
