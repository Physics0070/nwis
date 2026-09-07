/** NWIS design tokens.
 *
 *  Single source of truth for the visual layer. Components consume these tokens and
 *  never hardcode a colour or spacing value. When the Stitch design lands, this file
 *  and the component internals change; no API call, hook, route or state logic does.
 *
 *  The palette is a dark operations console: drilling monitoring is watched for long
 *  periods, often on rig displays, so contrast is high and chrome is quiet.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          base: "#0b1015",
          raised: "#121a22",
          overlay: "#18232d",
          border: "#243141",
          hover: "#1e2b38",
        },
        ink: {
          primary: "#e8eef5",
          secondary: "#9bacc0",
          muted: "#63788f",
        },
        accent: {
          DEFAULT: "#2f9dd6",
          strong: "#4fb4e6",
          soft: "#12303f",
        },
        // Alert levels. Names match the backend level vocabulary exactly, so a level
        // string from the API maps to a colour without a translation table.
        level: {
          INFO: "#4b7fa8",
          LOW: "#3aa0d1",
          MEDIUM: "#d99b34",
          HIGH: "#e2703a",
          CRITICAL: "#d9455f",
        },
        state: {
          ok: "#3fa87a",
          warn: "#d99b34",
          bad: "#d9455f",
        },
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Consolas", "ui-monospace", "monospace"],
      },
      borderRadius: {
        card: "10px",
        pill: "999px",
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.40), 0 8px 24px rgba(0,0,0,0.25)",
      },
      fontSize: {
        metric: ["1.75rem", { lineHeight: "2rem", letterSpacing: "-0.02em" }],
      },
    },
  },
  plugins: [],
};
