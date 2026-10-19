/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: { sans: ["system-ui", "-apple-system", '"Segoe UI"', "sans-serif"] },
      colors: {
        surface: "var(--surface-1)",
        page: "var(--page)",
        ink: "var(--text-primary)",
        ink2: "var(--text-secondary)",
        muted: "var(--text-muted)",
        line: "var(--gridline)",
        axis: "var(--axis)",
        accent: "var(--series-1)",
      },
    },
  },
  plugins: [],
};
