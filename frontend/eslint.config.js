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
      // stay errors and are fixed. Every historically-downgraded rule has now
      // been driven to error as its sites were cleaned up.
      //
      // exhaustive-deps is now an error: the last two flagged sites were the
      // same shape — an SWR-derived array (`data?.items ?? []` in ReleasesPage,
      // `users ?? []` in TestManagementPage) recreated as a fresh literal every
      // render and then used as a useMemo dependency, defeating the downstream
      // memo. Each was wrapped in its own useMemo (the fix the rule itself
      // recommends), matching the `projectMembers = useMemo(() => … ?? [], […])`
      // pattern already in those files.
      //
      // set-state-in-effect is now an error: every flagged site was migrated
      // off load/reset-state-in-effect — pagination/selection resets use the
      // adjust-state-during-render previous-value pattern, data fetches moved to
      // SWR hooks, and the two genuine cases that must stay effects (a network
      // token re-verification in ProtectedRoute, a coordinated one-time
      // deep-link expand+scroll in TestManagementPage) carry scoped disables.
      //
      // immutability is now an error: the only flagged cases were a
      // wrapper-hook ref not named with the "Ref" suffix the rule keys on,
      // and a self-referencing reconnect timer — both fixed by routing the
      // reconnect through a connectRef and renaming the ref.
      //
      // refs is now an error: every flagged case was a single component
      // (CasesFilterBar) that took its props as an undestructured `p` object
      // whose `searchInputRef` member made the rule treat *all* `p.*` reads as
      // ref-reads-during-render. Destructuring the props at the parameter keeps
      // the ref a named binding (forwarded to a DOM `ref=`, which is allowed)
      // and clears every false positive.
      ...reactHooks.configs['recommended-latest'].rules,
      'react-hooks/set-state-in-effect': 'error',
      'react-hooks/refs': 'error',
      'react-hooks/immutability': 'error',
      'react-hooks/exhaustive-deps': 'error',

      // React Refresh (Vite HMR)
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // TypeScript — relax rules that block rapid iteration
      //
      // no-explicit-any is now an error: every production site had already been
      // given a real type, leaving one last source case — useTableSort's generic
      // constraint `T extends Record<string, any>`, which only needs an indexable
      // shape. Its sort body already narrows with runtime `typeof` checks and a
      // `String()` fallback, so `Record<string, unknown>` types it precisely.
      // The single remaining `any` is a test-only `File.prototype` mock that is
      // genuinely unavoidable and carries a scoped disable. Promoting the rule to
      // error guards against reintroducing untyped `any` in app code.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      }],
      // no-non-null-assertion is now an error: every `x!` non-null assertion in
      // src/ was replaced with a real guard, early-return, default, or narrowing
      // as its owning page/area was cleaned up over successive ratchet passes,
      // leaving zero remaining sites. Promoting the rule to error guards against
      // reintroducing unchecked `!` assertions — which silence the compiler's
      // null/undefined analysis and turn a would-be type error into a runtime
      // crash — in app code.
      '@typescript-eslint/no-non-null-assertion': 'error',

      // Design-audit guard (handoff 1.3): raw Tailwind palette classes
      // (text-emerald-400, bg-red-900/40, …) bypass the per-theme token
      // system (--status-*, --gate-*, --color-*) and are each a light-theme
      // defect. Ratchet: 'warn' until the existing sites are tokenized
      // (design_handoff_ui_improvements Phase 1.1), then flip to 'error'.
      // Avatar-color swatches (user data, not theme UI) are exempt by intent.
      'no-restricted-syntax': ['warn', {
        selector: 'Literal[value=/(text|bg|border)-(emerald|green|red|amber|yellow|orange|purple|blue)-[0-9]{2,3}/]',
        message: 'Use theme tokens (--status-*, --gate-*, --color-*) instead of raw palette classes.',
      }],

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
