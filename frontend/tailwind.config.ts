import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#263238",
        muted: "#647276",
        line: "#dfe7e8",
        teal: "#008b8f",
        tealSoft: "#e9f7f6",
        amberSoft: "#fff7e4",
        coral: "#d96858"
      },
      boxShadow: {
        material: "0 1px 2px rgba(38, 50, 56, 0.08), 0 6px 20px rgba(38, 50, 56, 0.06)"
      }
    }
  },
  plugins: []
};

export default config;
