import next from "eslint-config-next";

// eslint-config-next 16 ships a native flat-config array
// (next/core-web-vitals + next/typescript).
const eslintConfig = [
  ...next,
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
];

export default eslintConfig;
