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
      // React Hooks — adopt the v7 recommended (React Compiler) rule set.
      // Real-bug rules (purity, static-components, set-state-in-render, etc.)
      // stay errors and are fixed. The rules below are downgraded to warn
      // because, on this codebase, they flag *intentional/valid* patterns the
      // React Compiler's conservative inference can't see through — forcing
      // their "fixes" would contort correct code:
      //   • set-state-in-effect — reset/load-state-on-prop-change effects
      //   • refs — forwarding a ref prop to a DOM node (never reads .current)
      //   • immutability — writing ref.current in an effect when the ref is
      //     returned from a wrapper hook, and self-referencing reconnect timers
      //   • exhaustive-deps — long-standing advisory
      // Tracked as a follow-up to revisit individually.
      ...reactHooks.configs['recommended-latest'].rules,
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/immutability': 'warn',
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
