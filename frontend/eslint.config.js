import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  // Ignore build output
  { ignores: ['dist', 'node_modules'] },

  // Base JS recommended rules
  js.configs.recommended,

  // TypeScript files
  {
    files: ['**/*.{ts,tsx}'],
    extends: [...tseslint.configs.recommended],
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // React Hooks — keep the long-standing rule behavior. v7 expands
      // `configs.recommended` with React-Compiler rules (purity,
      // set-state-in-effect, immutability) that flag many existing
      // components; adopting those is a separate refactor (follow-up).
      // Pin the two classic rules so the plugin upgrade is behavior-preserving.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      // React Refresh (Vite HMR)
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // TypeScript — relax rules that block rapid iteration
      '@typescript-eslint/no-explicit-any': 'warn',        // warn, not error
      '@typescript-eslint/no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      }],
      '@typescript-eslint/no-non-null-assertion': 'warn',

      // Allow console in development
      'no-console': 'off',
    },
  },

  // Plain JS/TS config files (vite, tailwind, postcss)
  {
    files: ['*.config.{js,ts,mjs,cjs}', '*.config.*.{js,ts}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
)
