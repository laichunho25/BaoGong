/** @type {import('tailwindcss').Config} */

// Semantic tokens, not raw palette names (see .claude/skills/frontend-page).
// A template that writes `bg-blue-500` has decided a colour; one that writes
// `bg-brand-600` has named a role, and the role can be repainted in one place.
//
// The palette is deliberately narrow: one brand hue (harbour teal), one accent
// used only for money and quotes (amber), and four state hues. Anything the
// register publishes is rendered in `official` grey and never in brand colour,
// so that "this came from the government" and "this is our own product" can
// never be confused by the reader (COMPLIANCE section 1).
module.exports = {
  content: [
    "./templates/**/*.html",
    "./apps/**/templates/**/*.html",
    "./apps/**/*.py",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eff9f8",
          100: "#d6efec",
          200: "#aedfda",
          300: "#7ac7c1",
          400: "#46a8a2",
          500: "#2a8c87",
          600: "#1f706e",
          700: "#1c5a59",
          800: "#1a4849",
          900: "#183d3e",
          950: "#0a2223",
        },
        accent: {
          50: "#fff8eb",
          100: "#feecc7",
          200: "#fdd88a",
          300: "#fcbe4d",
          400: "#fba524",
          500: "#f5830b",
          600: "#d95f06",
          700: "#b44009",
          800: "#92330e",
          900: "#782b0f",
        },
        // The neutral ramp. `ink` is text, `surface` is background, `line` is
        // borders - naming them apart stops a border and a heading from
        // drifting onto the same grey by accident.
        ink: {
          DEFAULT: "#0f172a",
          soft: "#334155",
          muted: "#64748b",
          faint: "#94a3b8",
        },
        surface: {
          DEFAULT: "#ffffff",
          sunken: "#f8fafc",
          raised: "#ffffff",
          inverse: "#0f172a",
        },
        line: {
          DEFAULT: "#e2e8f0",
          strong: "#cbd5e1",
        },
        // Official-register chrome. Grey on purpose, never brand.
        official: {
          bg: "#f1f5f9",
          border: "#cbd5e1",
          text: "#334155",
        },
        success: {
          50: "#ecfdf5",
          200: "#a7f3d0",
          600: "#059669",
          700: "#047857",
          900: "#064e3b",
        },
        warning: {
          50: "#fffbeb",
          200: "#fde68a",
          600: "#d97706",
          700: "#b45309",
          900: "#78350f",
        },
        danger: {
          50: "#fef2f2",
          200: "#fecaca",
          600: "#dc2626",
          700: "#b91c1c",
          900: "#7f1d1d",
        },
      },
      fontFamily: {
        // Self-hosted stack; system CJK fallbacks keep mainland users covered.
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px -12px rgba(15, 23, 42, 0.16)",
        lift: "0 2px 4px rgba(15, 23, 42, 0.06), 0 18px 40px -20px rgba(15, 23, 42, 0.30)",
      },
      backgroundImage: {
        // Used by the home hero only. A flat colour there made the page read as
        // a form; this is the one place the site is allowed to be loud.
        "brand-wash":
          "radial-gradient(1200px 400px at 15% -10%, #2a8c87 0%, transparent 60%), linear-gradient(160deg, #0a2223 0%, #1a4849 55%, #1f706e 100%)",
      },
      keyframes: {
        "rise-in": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        // Short and once. Motion here is to show order of arrival, not to
        // entertain; anything longer gets in the way of reading.
        "rise-in": "rise-in 400ms cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};
